import assert from "node:assert/strict"
import test from "node:test"
import { auditText, isAuditPage, type AuditEvent } from "../src/pages/audit/events.js"
import { participants, type Conversation } from "../src/pages/inbox/data.js"

const jam = "jam_CAPTAIN123"
const captain = `capy:captain:${jam}`

function event(action: string, overrides: Partial<AuditEvent> = {}): AuditEvent {
  return {
    id: 1, at: null, origin: "mcp", source: "codex", action,
    destination: "capy", outcome: "ok", target: null, ref: null, ...overrides,
  }
}

test("audit uses discovered labels and hides older raw Captain metadata", () => {
  const discovered = event("delegate", { target: captain, metadata: { target_label: "Conductor" } })
  assert.ok(isAuditPage({ items: [discovered], next_cursor: null }))
  assert.equal(auditText(discovered), "Delegated to Conductor")
  assert.equal(auditText(event("delegate", { target: captain })), "Delegated to Captain")
  assert.equal(auditText(event("delegate", { target: `captain Conductor (${jam})` })), "Delegated to Conductor")
  assert.equal(auditText(event("route_set", { metadata: { agent: "github", repo_count: 1, target_name: captain } })),
    "Routed GitHub (1 repo) to Captain")
  assert.equal(auditText(event("route_set", { metadata: { agent: "slack", target_name: jam } })), "Routed Slack to Captain")
  assert.equal(auditText(event("route_set", { metadata: { agent: "slack", target_name: "Conductor" } })), "Routed Slack to Conductor")
})

test("audit hides jam ids from fallback sentences", () => {
  assert.equal(auditText(event("turn_cap", { ref: captain, metadata: { turn_cap: 8 } })), "Paused conversation Captain at 8 turns")
  assert.equal(auditText(event("conversation_resume", { ref: jam, metadata: { ref_label: "Conductor" } })), "Resumed conversation Conductor")
  for (const row of [
    event("unknown_event", { destination: captain, outcome: `failed for ${jam}` }),
    event("webhook", { destination: jam }),
    event("request_write", { target: captain, outcome: "queued" }),
    event("route_remove", { destination: jam, project: jam, metadata: { agent: jam, state: jam } }),
  ]) assert.doesNotMatch(auditText(row), /jam_/i)
})

test("audit displays the key from project and general memory paths", () => {
  assert.equal(auditText(event("memory_write", { target: "project:context/notes/x" })), "Wrote memory x")
  assert.equal(auditText(event("memory_delete", { target: "general/research/archive/quick-start.md" })),
    "Deleted memory quick-start.md")
})

test("inbox participants prefer the Captain label and keep a human fallback", () => {
  const row: Conversation = {
    owner: "codex", handle: "h_test", destination: "capy", ref: jam, target: captain, project: null,
    title: null, status: null, open: true, created_at: null, updated_at: null,
  }
  assert.equal(participants({ ...row, first_target: "Conductor" }).to, "Conductor")
  assert.equal(participants(row).to, "Captain")
  assert.equal(participants({ ...row, target: null }).to, "Capy thread")
})
