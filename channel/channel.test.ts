import { expect, test } from "bun:test"
import { sseFrames, toEvent } from "./switchboard-channel.ts"

test("parses SSE frames split across chunks", async () => {
  const chunks = ["retry: 3000\nevent: ready\ndata: {\"cursor\":4}\n\n: c\nid: 5\nev", "ent: item\ndata: {\"a\":1}\n\nevent: heartbeat\ndata: {}\n\n"]
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      for (const s of chunks) c.enqueue(new TextEncoder().encode(s))
      c.close()
    },
  })
  const out = []
  for await (const f of sseFrames(body)) out.push(f)
  expect(out).toEqual([
    { event: "ready", id: undefined, data: "{\"cursor\":4}" },
    { event: "item", id: "5", data: "{\"a\":1}" },
    { event: "heartbeat", id: undefined, data: "{}" },
  ])
})

test("turns an inbox item into a channel event with identifier meta keys", () => {
  const e = toEvent({ cursor: 7, handle: "h_1", owner: "claude-code", kind: "final", text: "Done", thread_id: "jam_X", pull_requests: ["https://github.com/a/b/pull/1"] })
  expect(e.content).toBe("Done\n\nPull requests:\n- https://github.com/a/b/pull/1")
  expect(e.meta).toEqual({ kind: "final", handle: "h_1", agent: "claude-code", cursor: "7", thread_id: "jam_X" })
  for (const k of Object.keys(e.meta)) expect(/^[A-Za-z0-9_]+$/.test(k)).toBe(true)
})
