#!/usr/bin/env bun
// SwB channel for Claude Code (research preview): pushes Capy's replies to SwB delegations
// into the open Claude Code session. It reads the SwB inbox over SSE (GET /inbox/events) and turns each
// item into a notifications/claude/channel event. Events arrive only while the session is open; items that
// came in meanwhile are delivered on the next start, from the cursor saved in ~/.claude/channels/switchboard/.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs"
import { homedir } from "node:os"
import { basename, dirname, join } from "node:path"
import { Server } from "@modelcontextprotocol/sdk/server/index.js"
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js"

const log = (msg: string) => process.stderr.write(`[switchboard-channel] ${msg}\n`)

const mcp = new Server(
  { name: "swb", title: "SwB", version: "0.2.0" },
  {
    capabilities: { experimental: { "claude/channel": {} } },
    instructions:
      "Events from the SwB channel are Capy's reports on work delegated through SwB (capy_delegate). " +
      "kind is reply (progress or an answer), ask (Capy needs the owner), status, error, or final (the delegation finished). " +
      "Read them and tell the owner what changed. To answer Capy, use the SwB MCP server's capy_reply tool with the thread_id. " +
      "This channel delivers events to Claude Code.",
  },
)

function readCursor(file: string): string | null {
  try {
    const v = readFileSync(file, "utf8").trim()
    return /^\d+$/.test(v) ? v : null
  } catch {
    return null
  }
}

function saveCursor(file: string, v: string): void {
  mkdirSync(dirname(file), { recursive: true, mode: 0o700 })
  writeFileSync(file, v + "\n", { mode: 0o600 })
}

type Item = { cursor: number; handle: string; owner: string; kind: string; text?: string; thread_id?: string; status?: string; pull_requests?: string[] }

export function toEvent(item: Item): { content: string; meta: Record<string, string> } {
  const prs = item.pull_requests?.length ? "\n\nPull requests:\n" + item.pull_requests.map((u) => "- " + u).join("\n") : ""
  const body = item.text ?? (item.status ? `Thread status: ${item.status}` : "")
  const meta: Record<string, string> = { kind: item.kind, handle: item.handle, agent: item.owner, cursor: String(item.cursor) }
  if (item.thread_id) meta.thread_id = item.thread_id
  if (item.status) meta.status = item.status
  return { content: body + prs, meta }
}

export async function* sseFrames(body: ReadableStream<Uint8Array>): AsyncGenerator<{ event: string; id?: string; data: string }> {
  const reader = body.pipeThrough(new TextDecoderStream()).getReader()
  let buf = ""
  let event = "message"
  let id: string | undefined
  let data: string[] = []
  for (;;) {
    const { value, done } = await reader.read()
    if (done) return
    buf += value
    let nl: number
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).replace(/\r$/, "")
      buf = buf.slice(nl + 1)
      if (line === "") {
        if (data.length) yield { event, id, data: data.join("\n") }
        event = "message"
        id = undefined
        data = []
      } else if (!line.startsWith(":")) {
        const i = line.indexOf(":")
        const name = i < 0 ? line : line.slice(0, i)
        const val = i < 0 ? "" : line.slice(i + 1).replace(/^ /, "")
        if (name === "event") event = val
        else if (name === "data") data.push(val)
        else if (name === "id") id = val
      }
    }
  }
}

async function follow(url: string, key: string, agent: string, stateDir: string): Promise<never> {
  const cursorFile = join(stateDir, "cursor")
  let cursor = readCursor(cursorFile)
  let previous = cursor === null && stateDir === process.env.CLAUDE_PLUGIN_DATA && basename(stateDir) === "swb-swb"
    ? readCursor(join(dirname(stateDir), "switchboard-switchboard", "cursor"))
    : null
  let delay = 1000
  for (;;) {
    try {
      if (previous !== null) {
        saveCursor(cursorFile, previous)
        cursor = previous
        previous = null
      }
      const q = new URLSearchParams()
      if (agent) q.set("agent", agent)
      if (cursor) q.set("after", cursor)
      const res = await fetch(`${url}/inbox/events?${q}`, { headers: { Authorization: `Bearer ${key}`, Accept: "text/event-stream" } })
      if (res.status === 401) throw new Error("SwB rejected the channel key (401)")
      if (!res.ok || !res.body) throw new Error(`SwB answered ${res.status}`)
      log("Connected to SwB")
      for await (const f of sseFrames(res.body)) {
        delay = 1000
        if (f.event === "ready" && !cursor) {
          cursor = String(JSON.parse(f.data).cursor)
          saveCursor(cursorFile, cursor)
        }
        if (f.event !== "item") continue
        const item = JSON.parse(f.data) as Item
        await mcp.notification({ method: "notifications/claude/channel", params: toEvent(item) })
        cursor = f.id ?? String(item.cursor)
        saveCursor(cursorFile, cursor)
      }
      log("stream closed; reconnecting")
    } catch (e) {
      log(`${(e as Error).message}; retrying in ${Math.round(delay / 1000)}s`)
    }
    await new Promise((r) => setTimeout(r, delay))
    delay = Math.min(delay * 2, 60_000)
  }
}

if (import.meta.main) {
  const readEnv = (suffix: string) => {
    const name = `SWB_${suffix}`
    const value = process.env[name]
    if (value !== undefined) return value
    const legacy = `SWITCHBOARD_${suffix}`
    const old = process.env[legacy]
    if (old !== undefined) log(`Use ${name} in place of ${legacy}.`)
    return old
  }
  const url = (readEnv("URL") ?? "").trim().replace(/\/+$/, "")
  const key = readEnv("KEY")
  const agent = readEnv("AGENT") ?? ""
  const stateDir = readEnv("CHANNEL_STATE") ?? join(homedir(), ".claude", "channels", "switchboard")
  if (!url) {
    log("Set SWB_URL to the base URL of your SwB server")
    process.exit(1)
  }
  if (!key) {
    log("Set SWB_KEY to the value of SWB_KEY_CHANNEL from the server's .env")
    process.exit(1)
  }
  await mcp.connect(new StdioServerTransport())
  void follow(url, key, agent, stateDir)
}
