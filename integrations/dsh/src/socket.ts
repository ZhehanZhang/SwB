import { once } from 'node:events'
import { chmodSync, lstatSync, mkdirSync, unlinkSync } from 'node:fs'
import { createConnection, createServer, type Socket } from 'node:net'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'

export type State = 'idle' | 'working' | 'blocked' | 'done' | 'ended'
export type BridgeEvent =
  | { event: 'state'; session: string; state: State }
  | { event: 'final'; session: string; text: string; messageIds?: string[] }
type Session = { id: string; name: string; dir: string; state: State }
type PromptResult = { result: { receipt: { messageId: string } } } | { error: { code: 'agent_blocked' | 'agent_not_running' } }

export interface Bridge {
  list(): Session[]
  prompt(session: string, text: string): PromptResult
}

const maxFrame = 1024 * 1024
const ioTimeout = 5_000

export function socketPath(): string {
  const root = process.env.SWB_COMPANION_HOME || join(homedir(), '.config', 'swb')
  return join(root.startsWith('~/') ? join(homedir(), root.slice(2)) : root, 'dsh.sock')
}

async function clearStaleSocket(path: string, uid: number): Promise<void> {
  const original = lstatSync(path, { throwIfNoEntry: false })
  if (!original) return
  if (!original.isSocket() || original.uid !== uid) throw new Error('Choose a private SwB socket path.')
  const stale = await new Promise<boolean>(resolve => {
    const probe = createConnection(path)
    const done = (stale: boolean) => {
      probe.destroy()
      resolve(stale)
    }
    probe.once('connect', () => done(false))
    probe.once('error', (error: NodeJS.ErrnoException) => done(error.code === 'ECONNREFUSED'))
    probe.setTimeout(ioTimeout, () => done(false))
  })
  if (!stale) throw new Error('The SwB bridge socket is in use.')
  const current = lstatSync(path, { throwIfNoEntry: false })
  if (current?.ino === original.ino && current.dev === original.dev) unlinkSync(path)
}

export async function serve(path: string, bridge: Bridge) {
  const uid = process.getuid?.()
  if (uid === undefined) throw new Error('The SwB bridge needs a Unix socket.')
  const dir = dirname(path)
  mkdirSync(dir, { recursive: true, mode: 0o700 })
  const info = lstatSync(dir)
  if (!info.isDirectory() || info.uid !== uid) throw new Error('Choose a private SwB companion directory.')
  chmodSync(dir, 0o700)
  await clearStaleSocket(path, uid)

  const clients = new Set<Socket>()
  const subscribers = new Set<Socket>()
  const server = createServer(socket => {
    clients.add(socket)
    socket.on('error', () => socket.destroy())
    socket.on('close', () => {
      clients.delete(socket)
      subscribers.delete(socket)
    })
    socket.setTimeout(ioTimeout, () => socket.destroy())
    let pending = Buffer.alloc(0)
    const send = (value: object) => socket.end(JSON.stringify(value) + '\n')
    const receive = (chunk: Buffer) => {
      const lf = chunk.indexOf(10)
      if (pending.length + (lf === -1 ? chunk.length : lf) >= maxFrame) {
        socket.destroy()
        return
      }
      pending = Buffer.concat([pending, lf === -1 ? chunk : chunk.subarray(0, lf)])
      if (lf === -1) return
      socket.off('data', receive)
      let request: unknown
      try {
        request = JSON.parse(pending.toString('utf8'))
      } catch {
        send({ error: { code: 'invalid_request' } })
        return
      } finally {
        pending = Buffer.alloc(0)
      }
      if (!request || typeof request !== 'object') {
        send({ error: { code: 'invalid_request' } })
        return
      }
      const { method, session, text } = request as Record<string, unknown>
      try {
        if (method === 'subscribe') {
          socket.setTimeout(0)
          socket.on('data', () => socket.destroy())
          socket.write('{"result":{"subscribed":true}}\n')
          subscribers.add(socket)
        } else if (method === 'list') {
          send({ result: { sessions: bridge.list() } })
        } else if (method === 'prompt' && typeof session === 'string' && typeof text === 'string' && [...text].length <= 60_000) {
          send(bridge.prompt(session, text))
        } else {
          send({ error: { code: 'invalid_request' } })
        }
      } catch {
        send({ error: { code: 'request_failed' } })
      }
    }
    socket.on('data', receive)
  })

  const close = async () => {
    for (const socket of clients) socket.destroy()
    await new Promise<void>(resolve => server.close(() => resolve()))
  }
  const listening = once(server, 'listening')
  server.listen(path)
  await listening
  try {
    chmodSync(path, 0o600)
  } catch (error) {
    await close()
    throw error
  }

  return {
    close,
    publish(event: BridgeEvent) {
      if (!subscribers.size) return
      const frame = Buffer.from(JSON.stringify(event) + '\n')
      for (const socket of subscribers) {
        if (socket.writableLength + frame.length > maxFrame) socket.destroy()
        else socket.write(frame)
      }
    },
  }
}
