import assert from 'node:assert/strict'
import { test } from 'node:test'
import { adapt, inject } from '../src/adapter.ts'
import type { BridgeEvent } from '../src/socket.ts'

function agent(id: string, origin?: string) {
  const messages: { method: string; message: { id: string; content: unknown } }[] = []
  return {
    id, status: 'idle', session: { id, header: { cwd: '/private/source/project/', origin } },
    messages,
    followup(message: (typeof messages)[number]['message']) { messages.push({ method: 'followup', message }) },
    steer(message: (typeof messages)[number]['message']) { messages.push({ method: 'steer', message }) },
  }
}

function fixture() {
  const root = agent('session-1')
  const child = agent('child-1', 'subagent')
  const agents = new Map([[root.id, root], [child.id, child]])
  const listeners = new Map<string, (...args: unknown[]) => void>()
  const events: BridgeEvent[] = []
  const effects: (() => void)[] = []
  const pending: { agentId: string; event: string }[] = []
  const ctx = {
    agents: { list: () => [...agents.values()], get: (id: string) => agents.get(id) },
    typertGateway: { getPendingRemoteEvents: () => pending },
    on(name: string, listener: (...args: unknown[]) => void, options: { global: boolean }) {
      assert.equal(options.global, true)
      listeners.set(name, listener)
    },
    effect(start: () => () => void) { effects.push(start()) },
  }
  const bridge = adapt(ctx as unknown as Parameters<typeof adapt>[0], event => events.push(event))
  const fire = (name: string, ...args: unknown[]) => {
    assert.ok(listeners.has(name), name)
    listeners.get(name)!(...args)
  }
  return {
    root, agents, bridge, events, pending, fire,
    commit: (type: string, data?: unknown, surfaceOp?: string) => fire('session/event', root.session, { type, data, surfaceOp }),
    close: () => effects.forEach(close => close()),
  }
}

test('lists only live roots and emits state and final committed assistant text', t => {
  const f = fixture()
  t.after(f.close)
  assert.deepEqual(inject, ['agents', 'typertGateway'])
  assert.deepEqual(f.bridge.list(), [{ id: f.root.id, name: 'project', dir: 'project', state: 'idle' }])
  f.root.status = 'running'
  f.fire('agent/status', { agent: f.root, status: f.root.status })
  f.commit('turn/start')
  f.commit('assistant/message', { message: { content: [{ type: 'text', text: 'draft' }] } }, 'append')
  f.commit('assistant/message', { message: { content: [
    { type: 'text', text: 'final ' }, { type: 'reasoning', text: 'secret' }, { type: 'text', text: 'answer' },
  ] } }, 'append')
  f.commit('assistant/message', { message: { content: [{ type: 'text', text: 'historical replacement' }] } }, 'replace')
  f.commit('assistant/attempt', { stream: [{ type: 'text-delta', text: 'failed' }] })
  assert.ok(f.events.every(event => event.event === 'state'))
  f.commit('turn/end')
  f.root.status = 'idle'
  f.fire('agent/status', { agent: f.root, status: f.root.status })
  assert.equal(f.bridge.list()[0].state, 'done')
  f.agents.delete(f.root.id)
  f.fire('agent/disposed', { agent: f.root })
  assert.deepEqual(f.bridge.list(), [])
  assert.deepEqual(f.events, [
    { event: 'state', session: f.root.id, state: 'idle' },
    { event: 'state', session: f.root.id, state: 'working' },
    { event: 'state', session: f.root.id, state: 'done' },
    { event: 'final', session: f.root.id, text: 'final answer' },
    { event: 'state', session: f.root.id, state: 'ended' },
  ])
})

test('prompts only a live unblocked agent through followup or steer', t => {
  const f = fixture()
  t.after(f.close)
  assert.deepEqual(f.bridge.prompt('missing', 'refused'), { error: { code: 'agent_not_running' } })
  assert.deepEqual(f.bridge.prompt('child-1', 'refused'), { error: { code: 'agent_not_running' } })
  f.pending.push({ agentId: f.root.id, event: 'user-questions/request' })
  assert.deepEqual(f.bridge.prompt(f.root.id, 'refused'), { error: { code: 'agent_blocked' } })
  assert.equal(f.bridge.list()[0].state, 'blocked')
  f.pending.pop()
  f.commit('approval/asked', { id: 'approval-1' })
  f.commit('approval/asked', { id: 'approval-2' })
  f.commit('approval/decided', { id: 'approval-1' })
  assert.deepEqual(f.bridge.prompt(f.root.id, 'refused'), { error: { code: 'agent_blocked' } })
  f.commit('approval/decided', { id: 'approval-2' })
  assert.equal(f.bridge.list()[0].state, 'idle')
  assert.deepEqual(f.root.messages, [])
  const delivered = f.bridge.prompt(f.root.id, 'hello\nworld')
  assert.equal(f.root.messages[0].method, 'followup')
  assert.deepEqual(f.root.messages[0].message.content, [{ type: 'text', text: 'hello\nworld' }])
  assert.deepEqual(delivered, { result: { receipt: { messageId: f.root.messages[0].message.id } } })
  f.root.status = 'running'
  f.pending.push({ agentId: f.root.id, event: 'approval/request' })
  assert.deepEqual(f.bridge.prompt(f.root.id, 'refused'), { error: { code: 'agent_blocked' } })
  f.pending.pop()
  f.bridge.prompt(f.root.id, 'while working')
  assert.deepEqual(f.root.messages.map(entry => entry.method), ['followup', 'steer'])
})

test('attributes a final only to SwB input committed before that assistant text', t => {
  const f = fixture()
  t.after(f.close)
  f.root.status = 'running'
  const text = (value: string) =>
    f.commit('assistant/message', { message: { content: [{ type: 'text', text: value }] } }, 'append')
  const claim = () => f.fire('agent/inbox/claimed', { agent: f.root, message: f.root.messages.at(-1)!.message })
  const commit = () => f.commit('user/message', f.root.messages.at(-1)!.message, 'append')

  f.commit('turn/start')
  text('the manual turn')
  f.bridge.prompt(f.root.id, 'steering message')
  f.commit('turn/end')
  assert.deepEqual(f.events.at(-1), { event: 'final', session: f.root.id, text: 'the manual turn' })

  f.commit('turn/start')
  claim()
  commit()
  text('reply to SwB')
  f.bridge.prompt(f.root.id, 'late steering message')
  claim()
  commit()
  f.commit('turn/end')
  assert.deepEqual(f.events.at(-1), {
    event: 'final', session: f.root.id, text: 'reply to SwB', messageIds: [f.root.messages[0].message.id],
  })

  f.commit('turn/start')
  text('another manual turn')
  f.commit('turn/end')
  assert.deepEqual(f.events.at(-1), { event: 'final', session: f.root.id, text: 'another manual turn' })
})
