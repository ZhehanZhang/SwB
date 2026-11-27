import { expect, test } from "bun:test"
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { Client } from "@modelcontextprotocol/sdk/client/index.js"
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js"
import type { Notification } from "@modelcontextprotocol/sdk/types.js"
import marketplace from "../.claude-plugin/marketplace.json"
import plugin from "./.claude-plugin/plugin.json"
import pkg from "./package.json"
import { sseFrames, toEvent } from "./switchboard-channel.ts"

const command = [process.execPath, "--no-env-file", "./switchboard-channel.ts"]

test("uses the swb marketplace, plugin, server and channel package names", () => {
  expect(marketplace.name).toBe("swb")
  expect(marketplace.plugins[0].name).toBe("swb")
  expect(plugin.name).toBe("swb")
  expect(plugin.channels[0].server).toBe("swb")
  expect(plugin.mcpServers.swb.env).toEqual({
    SWB_URL: "${user_config.url}",
    SWB_KEY: "${user_config.key}",
    SWB_CHANNEL_STATE: "${CLAUDE_PLUGIN_DATA}",
  })
  expect(pkg.name).toBe("swb-channel")
  expect(pkg.bin["swb-channel"]).toBe("./switchboard-channel.ts")
})

test("an explicitly empty SwB URL takes precedence over the old name", () => {
  const result = Bun.spawnSync(command, {
    cwd: import.meta.dir,
    env: { SWB_URL: "", SWITCHBOARD_URL: "https://old.example.test", SWB_KEY: "fixture-key" },
  })
  expect(result.exitCode).toBe(1)
  expect(result.stderr.toString()).toBe("[switchboard-channel] Set SWB_URL to the base URL of your SwB server\n")
})

test.each([false, true])("uses legacy names only when needed with either standalone state path (%s)", async (customState) => {
  const home = mkdtempSync(join(tmpdir(), "swb-channel-"))
  const state = customState ? join(home, "custom-state") : join(home, ".claude", "channels", "switchboard")
  const request = Promise.withResolvers<Request>()
  const server = Bun.serve({
    hostname: "127.0.0.1",
    port: 0,
    fetch(req) {
      request.resolve(req)
      return new Response(null, { status: 401 })
    },
  })
  mkdirSync(state, { recursive: true })
  writeFileSync(join(state, "cursor"), "28\n")
  const child = Bun.spawn(command, {
    cwd: import.meta.dir,
    env: {
      HOME: home,
      SWITCHBOARD_URL: String(server.url),
      SWB_KEY: "fixture-key",
      SWITCHBOARD_KEY: "unused-fixture-key",
      SWITCHBOARD_AGENT: "fixture-agent",
      ...(customState ? { SWITCHBOARD_CHANNEL_STATE: state } : {}),
    },
    stderr: "pipe",
    stdout: "ignore",
  })
  try {
    const req = await Promise.race([request.promise, child.exited.then(() => { throw new Error("Channel exited before following the inbox") })])
    expect(new URL(req.url).pathname).toBe("/inbox/events")
    expect(new URL(req.url).searchParams.toString()).toBe("agent=fixture-agent&after=28")
    expect(req.headers.get("Authorization")).toBe("Bearer fixture-key")
  } finally {
    child.kill()
    await child.exited
    server.stop(true)
    rmSync(home, { recursive: true, force: true })
  }
  const stderr = await new Response(child.stderr).text()
  expect(stderr.split("\n").filter((line) => line.includes("in place of"))).toEqual([
    "[switchboard-channel] Use SWB_URL in place of SWITCHBOARD_URL.",
    "[switchboard-channel] Use SWB_AGENT in place of SWITCHBOARD_AGENT.",
    ...(customState ? ["[switchboard-channel] Use SWB_CHANNEL_STATE in place of SWITCHBOARD_CHANNEL_STATE."] : []),
  ])
  expect(stderr).not.toContain("fixture-agent")
  expect(stderr).not.toContain("fixture-key")
  expect(stderr).not.toContain(state)
  expect(stderr).not.toContain(String(server.url))
})

test.each([undefined, 84])("uses the saved plugin cursor or carries forward the old one (%s)", async (current) => {
  const data = mkdtempSync(join(tmpdir(), "swb-plugin-"))
  const previous = join(data, "switchboard-switchboard")
  const state = join(data, "swb-swb")
  mkdirSync(previous)
  writeFileSync(join(previous, "cursor"), "41\n")
  if (current !== undefined) {
    mkdirSync(state)
    writeFileSync(join(state, "cursor"), `${current}\n`)
  }
  const start = current ?? 41
  const next = start + 1
  const item = { cursor: next, handle: "h_1", owner: "claude-code", kind: "final", text: "Done" }
  const send = Promise.withResolvers<void>()
  const request = Promise.withResolvers<Request>()
  const reconnect = Promise.withResolvers<Request>()
  const event = Promise.withResolvers<Notification>()
  let connected = false
  const server = Bun.serve({
    hostname: "127.0.0.1",
    port: 0,
    async fetch(req) {
      if (connected) {
        reconnect.resolve(req)
        return new Response(null, { status: 204 })
      }
      connected = true
      request.resolve(req)
      await send.promise
      return new Response(`event: ready\ndata: {"cursor":${next}}\n\nevent: item\nid: ${next}\ndata: ${JSON.stringify(item)}\n\n`, {
        headers: { "Content-Type": "text/event-stream" },
      })
    },
  })
  const client = new Client({ name: "test", version: "1.0.0" })
  client.fallbackNotificationHandler = async (message) => event.resolve(message)
  const transport = new StdioClientTransport({
    command: command[0],
    args: command.slice(1),
    cwd: import.meta.dir,
    env: {
      CLAUDE_PLUGIN_DATA: state,
      SWB_CHANNEL_STATE: state,
      SWITCHBOARD_CHANNEL_STATE: previous,
      SWB_URL: String(server.url),
      SWITCHBOARD_KEY: "fixture-key",
    },
    stderr: "pipe",
  })
  try {
    await client.connect(transport)
    expect(client.getServerVersion()).toEqual({ name: "swb", title: "SwB", version: pkg.version })
    expect(client.getServerCapabilities()?.experimental).toEqual({ "claude/channel": {} })
    const req = await request.promise
    expect(new URL(req.url).searchParams.get("after")).toBe(String(start))
    expect(req.headers.get("Authorization")).toBe("Bearer fixture-key")
    expect(readFileSync(join(state, "cursor"), "utf8")).toBe(`${start}\n`)
    send.resolve()
    expect(await event.promise).toEqual({ jsonrpc: "2.0", method: "notifications/claude/channel", params: toEvent(item) })
    expect(new URL((await reconnect.promise).url).searchParams.get("after")).toBe(String(next))
    expect(readFileSync(join(state, "cursor"), "utf8")).toBe(`${next}\n`)
    expect(readFileSync(join(previous, "cursor"), "utf8")).toBe("41\n")
  } finally {
    send.resolve()
    if (transport.pid !== null) process.kill(transport.pid)
    await client.close()
    server.stop(true)
    rmSync(data, { recursive: true, force: true })
  }
})

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
  const e = toEvent({ cursor: 7, handle: "h_1", owner: "claude-code", kind: "final", text: "Done", thread_id: "jam_X", pull_requests: ["https://github.com/example-org/example-repo/pull/1"] })
  expect(e.content).toBe("Done\n\nPull requests:\n- https://github.com/example-org/example-repo/pull/1")
  expect(e.meta).toEqual({ kind: "final", handle: "h_1", agent: "claude-code", cursor: "7", thread_id: "jam_X" })
  for (const k of Object.keys(e.meta)) expect(/^[A-Za-z0-9_]+$/.test(k)).toBe(true)
})
