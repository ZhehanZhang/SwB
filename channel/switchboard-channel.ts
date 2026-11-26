#!/usr/bin/env bun
// switchboard channel for Claude Code (research preview): pushes Capy's replies to switchboard delegations
// into the open Claude Code session. It reads switchboard's inbox over SSE (GET /inbox/events) and turns each
// item into a notifications/claude/channel event. Events arrive only while the session is open; items that
// came in meanwhile are delivered on the next start, from the cursor saved in ~/.claude/channels/switchboard/.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"
import { Server } from "@modelcontextprotocol/sdk/server/index.js"
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js"

const URL_BASE = (process.env.SWITCHBOARD_URL ?? "").trim().replace(/\/+$/, "")
const KEY = process.env.SWITCHBOARD_KEY ?? ""
const AGENT = process.env.SWITCHBOARD_AGENT ?? ""
const STATE_DIR = process.env.SWITCHBOARD_CHANNEL_STATE ?? join(homedir(), ".claude", "channels", "switchboard")
const CURSOR_FILE = join(STATE_DIR, "cursor")

const log = (msg: string) => process.stderr.write(`[switchboard-channel] ${msg}\n`)

const mcp = new Server(
  { name: "switchboard", version: "0.2.0" },
  {
    capabilities: { experimental: { "claude/channel": {} } },
    instructions:
      "Events from the switchboard channel are Capy's reports on work delegated through switchboard (capy_delegate). " +
      "kind is reply (progress or an answer), ask (Capy needs the owner), status, error, or final (the delegation finished). " +
      "Read them and tell the owner what changed. To answer Capy, use the switchboard MCP server's capy_reply tool with the thread_id; " +
      "this channel itself is one-way.",
  },
)

function readCursor(): string | null {
  try {
    const v = readFileSync(CURSOR_FILE, "utf8").trim()
    return /^\d+$/.test(v) ? v : null
  } catch {
    return null
  }
}

function saveCursor(v: string): void {
  mkdirSync(STATE_DIR, { recursive: true, mode: 0o700 })
  writeFileSync(CURSOR_FILE, v + "\n", { mode: 0o600 })
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

async function follow(): Promise<never> {
  let cursor = readCursor()
  let delay = 1000
  for (;;) {
    try {
      const q = new URLSearchParams()
      if (AGENT) q.set("agent", AGENT)
      if (cursor) q.set("after", cursor)
      const res = await fetch(`${URL_BASE}/inbox/events?${q}`, { headers: { Authorization: `Bearer ${KEY}`, Accept: "text/event-stream" } })
      if (res.status === 401) throw new Error("switchboard rejected SWITCHBOARD_KEY (401)")
      if (!res.ok || !res.body) throw new Error(`switchboard answered ${res.status}`)
      log(`connected to ${URL_BASE}`)
      for await (const f of sseFrames(res.body)) {
        delay = 1000
        if (f.event === "ready" && !cursor) {
          cursor = String(JSON.parse(f.data).cursor)
          saveCursor(cursor)
        }
        if (f.event !== "item") continue
        const item = JSON.parse(f.data) as Item
        await mcp.notification({ method: "notifications/claude/channel", params: toEvent(item) })
        cursor = f.id ?? String(item.cursor)
        saveCursor(cursor)
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
  if (!URL_BASE) {
    log("SWITCHBOARD_URL is not set; set it to the base URL of your Switchboard server")
    process.exit(1)
  }
  if (!KEY) {
    log("SWITCHBOARD_KEY is not set; set it to the value of SWITCHBOARD_KEY_CHANNEL from the server's .env")
    process.exit(1)
  }
  await mcp.connect(new StdioServerTransport())
  void follow()
}
