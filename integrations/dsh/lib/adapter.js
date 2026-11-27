import { createUserMessage } from '@deepseek-ai/dsh-llm';
import { serve, socketPath } from "./socket.js";
export const name = 'swb-dsh-bridge';
export const inject = ['agents', 'typertGateway'];
const validId = /^[A-Za-z0-9_.:-]{1,160}$/;
const visible = (agent) => agent.session.header.origin !== 'subagent' && validId.test(agent.id) && agent.id !== '.' && agent.id !== '..';
export function adapt(ctx, publish) {
    const tracked = new WeakMap();
    const track = (agent) => {
        let record = tracked.get(agent);
        if (!record) {
            record = { approvals: new Set(), done: false, text: '', messageIds: [], inputs: new Map() };
            tracked.set(agent, record);
        }
        return record;
    };
    const remoteBlocks = () => new Set(ctx.typertGateway.getPendingRemoteEvents()
        .filter(({ event }) => event === 'approval/request' || event === 'user-questions/request')
        .map(({ agentId }) => String(agentId)));
    const stateOf = (agent, blocks) => {
        const record = track(agent);
        if (record.approvals.size || blocks.has(agent.id))
            return 'blocked';
        if (record.done)
            return 'done';
        return agent.status === 'running' ? 'working' : 'idle';
    };
    const report = (agent, blocks = remoteBlocks()) => {
        const record = track(agent);
        const state = stateOf(agent, blocks);
        if (state !== record.state) {
            record.state = state;
            publish({ event: 'state', session: agent.id, state });
        }
        return state;
    };
    const list = () => {
        const blocks = remoteBlocks();
        return ctx.agents.list().filter(visible).map(agent => {
            const dir = (agent.session.header.cwd ?? '').split(/[\\/]/).filter(Boolean).at(-1) ?? '';
            const label = /^[^\p{C}:]{1,64}$/u.test(dir) && dir !== '.' && dir !== '..' ? dir : '';
            return { id: agent.id, name: label || agent.id, dir: label, state: report(agent, blocks) };
        });
    };
    ctx.on('agent/status', ({ agent, status }) => {
        if (!visible(agent) || ctx.agents.get(agent.id) !== agent)
            return;
        if (status === 'running')
            track(agent).done = false;
        report(agent);
    }, { global: true });
    ctx.on('agent/disposed', ({ agent }) => {
        if (!tracked.delete(agent))
            return;
        publish({ event: 'state', session: agent.id, state: 'ended' });
    }, { global: true });
    ctx.on('agent/inbox/claimed', ({ agent, message }) => {
        const inputs = tracked.get(agent)?.inputs;
        if (inputs?.has(message.id))
            inputs.set(message.id, 'claimed');
    }, { global: true });
    ctx.on('agent/inbox/discarded', ({ agent, message }) => {
        tracked.get(agent)?.inputs.delete(message.id);
    }, { global: true });
    ctx.on('session/event', (session, event) => {
        const agent = ctx.agents.get(session.id);
        if (!agent || agent.session !== session || !visible(agent))
            return;
        const record = track(agent);
        switch (event.type) {
            case 'turn/start':
                record.done = false;
                record.text = '';
                record.messageIds = [];
                break;
            case 'approval/asked':
                record.approvals.add(event.data.id);
                break;
            case 'approval/decided':
                record.approvals.delete(event.data.id);
                break;
            case 'user/message':
                if (event.surfaceOp === 'append' && record.inputs.get(event.data.id) === 'claimed') {
                    record.inputs.set(event.data.id, 'committed');
                }
                return;
            case 'assistant/message': {
                if (event.surfaceOp !== 'append')
                    return;
                const text = event.data.message.content.flatMap(part => part.type === 'text' ? [part.text] : []).join('');
                if (text.trim()) {
                    record.text = text.slice(0, 12_000).toWellFormed();
                    record.messageIds = [...record.inputs].filter(([, phase]) => phase === 'committed').map(([id]) => id);
                }
                return;
            }
            case 'turn/end': {
                record.done = true;
                record.approvals.clear();
                const { text, messageIds } = record;
                record.text = '';
                record.messageIds = [];
                for (const [id, phase] of record.inputs) {
                    if (phase !== 'queued')
                        record.inputs.delete(id);
                }
                report(agent);
                if (text)
                    publish({ event: 'final', session: agent.id, text, ...(messageIds.length ? { messageIds } : {}) });
                return;
            }
            default:
                return;
        }
        report(agent);
    }, { global: true });
    ctx.effect(() => {
        const timer = setInterval(() => {
            const blocks = remoteBlocks();
            for (const agent of ctx.agents.list().filter(visible))
                report(agent, blocks);
        }, 500).unref();
        return () => clearInterval(timer);
    });
    return {
        list,
        prompt(session, text) {
            const agent = validId.test(session) ? ctx.agents.get(session) : undefined;
            if (!agent || !visible(agent))
                return { error: { code: 'agent_not_running' } };
            if (stateOf(agent, remoteBlocks()) === 'blocked')
                return { error: { code: 'agent_blocked' } };
            const message = createUserMessage({ content: [{ type: 'text', text }], source: { kind: 'user' } });
            const { inputs } = track(agent);
            inputs.set(message.id, 'queued');
            try {
                if (agent.status === 'running')
                    agent.steer(message);
                else
                    agent.followup(message);
            }
            catch (error) {
                inputs.delete(message.id);
                throw error;
            }
            return { result: { receipt: { messageId: message.id } } };
        },
    };
}
export async function apply(ctx) {
    let publish = () => { };
    const bridge = adapt(ctx, event => publish(event));
    await ctx.effect(async () => {
        const socket = await serve(socketPath(), bridge);
        publish = socket.publish;
        return socket.close;
    });
}
