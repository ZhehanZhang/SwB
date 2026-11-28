import assert from 'node:assert/strict'
import { lstatSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { createConnection } from 'node:net'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createInterface } from 'node:readline'
import { test } from 'node:test'
import { serve, type Bridge } from '../src/socket.ts'

const bridge: Bridge = {
  list: () => [{ id: 's', name: 'name', dir: 'folder', state: 'idle' }],
  prompt: () => ({ result: { receipt: { messageId: 'receipt' } } }),
}

test('serves only the three socket methods and cleans up on close', { skip: process.platform === 'win32' }, async t => {
  const root = mkdtempSync(join(tmpdir(), 'swb-dsh-'))
  const dir = join(root, 'private')
  const path = join(dir, 'dsh.sock')
  const server = await serve(path, bridge)
  t.after(async () => {
    await server.close()
    rmSync(root, { recursive: true, force: true })
  })
  assert.equal(lstatSync(dir).mode & 0o777, 0o700)
  assert.equal(lstatSync(path).mode & 0o777, 0o600)
  await assert.rejects(serve(path, bridge), /in use/)
  const connect = (request: object) => {
    const client = createConnection(path)
    const lines = createInterface({ input: client })[Symbol.asyncIterator]()
    client.write(JSON.stringify(request) + '\n')
    return { client, read: async () => JSON.parse((await lines.next()).value) }
  }
  assert.deepEqual(await connect({ method: 'list' }).read(), { result: { sessions: bridge.list() } })
  assert.deepEqual(await connect({ method: 'prompt', session: 's', text: 'hello' }).read(), bridge.prompt('s', 'hello'))
  assert.deepEqual(await connect({ method: 'dispose' }).read(), { error: { code: 'invalid_request' } })
  const subscriber = connect({ method: 'subscribe' })
  assert.deepEqual(await subscriber.read(), { result: { subscribed: true } })
  const event = { event: 'final', session: 's', text: 'local test' } as const
  server.publish(event)
  assert.deepEqual(await subscriber.read(), event)
  await server.close()
  assert.throws(() => lstatSync(path), { code: 'ENOENT' })
  writeFileSync(path, 'keep')
  await assert.rejects(serve(path, bridge), /private SwB socket path/)
})
