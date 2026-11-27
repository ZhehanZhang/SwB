import { queryOptions } from "@tanstack/react-query"
import { requestJson } from "@/lib/api"

export type LampState = "on" | "off" | "fault"

export type Endpoint = {
  id: string
  label: string
  kind: string
  short_label: string | null
  detail: string | null
  avatar_url?: string | null
}

type Placement = Endpoint & {
  role: string
  state: LampState | null
  by: Endpoint | null
  at: string | null
}

type Project = {
  slug: string
  scope: string
  name: string
  archived: boolean
  created_at: string | null
  turn_cap: number | null
  memory_count: number
  open_count: number
}

export type Board = {
  runtime: { mcp_url: string | null; online: boolean; host: string; version: string | null; uptime_seconds: number }
  projects: (Project & { sources: Endpoint[]; agents: Placement[] })[]
  unassigned: Endpoint[]
}

export type ProjectRoute = {
  id: string
  from: Endpoint
  to: Endpoint
  direction: "route" | "reply"
  by: Endpoint | null
  at: string | null
  state: LampState | null
  delivery: string | null
}

type History = {
  id: number
  at: string | null
  action: string
  by: Endpoint | null
  destination: string
  outcome: string
}

type Conversation = { handle: string; endpoint: Endpoint; title: string | null; at: string | null }
type Note = { id: string; key: string; title: string; updated_at: string | null; by: Endpoint | null }

export type ProjectBoard = {
  project: Project
  claude_project: string | null
  routes: ProjectRoute[]
  conversations: Conversation[]
  history: History[]
  memory: Note[]
}

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function text(value: unknown): value is string {
  return typeof value === "string"
}

function count(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0
}

function nullable<T>(value: unknown, check: (value: unknown) => value is T): value is T | null {
  return value === null || check(value)
}

function list<T>(value: unknown, check: (value: unknown) => value is T): value is T[] {
  return Array.isArray(value) && value.every(check)
}

function state(value: unknown): value is LampState {
  return value === "on" || value === "off" || value === "fault"
}

function endpoint(value: unknown): value is Endpoint {
  return object(value) && text(value.id) && text(value.label) && text(value.kind)
    && nullable(value.short_label, text) && nullable(value.detail, text) && (value.avatar_url == null || text(value.avatar_url))
}

function placement(value: unknown): value is Placement {
  return object(value) && text(value.role) && nullable(value.state, state)
    && nullable(value.by, endpoint) && nullable(value.at, text) && endpoint(value)
}

function project(value: unknown): value is Project {
  return object(value) && text(value.slug) && text(value.name) && text(value.scope)
    && typeof value.archived === "boolean" && nullable(value.created_at, text)
    && nullable(value.turn_cap, count) && count(value.memory_count) && count(value.open_count)
}

function summary(value: unknown): value is Board["projects"][number] {
  return object(value) && list(value.sources, endpoint) && list(value.agents, placement) && project(value)
}

function board(value: unknown): value is Board {
  return object(value) && object(value.runtime) && nullable(value.runtime.mcp_url, text)
    && typeof value.runtime.online === "boolean" && text(value.runtime.host) && nullable(value.runtime.version, text)
    && count(value.runtime.uptime_seconds)
    && list(value.projects, summary) && list(value.unassigned, endpoint)
}

function route(value: unknown): value is ProjectRoute {
  return object(value) && text(value.id) && endpoint(value.from) && endpoint(value.to)
    && (value.direction === "route" || value.direction === "reply")
    && nullable(value.by, endpoint) && nullable(value.at, text) && nullable(value.state, state)
    && nullable(value.delivery, text)
}

function history(value: unknown): value is History {
  return object(value) && count(value.id) && nullable(value.at, text) && text(value.action)
    && nullable(value.by, endpoint) && text(value.destination) && text(value.outcome)
}

function conversation(value: unknown): value is Conversation {
  return object(value) && text(value.handle) && endpoint(value.endpoint) && nullable(value.title, text) && nullable(value.at, text)
}

function note(value: unknown): value is Note {
  return object(value) && text(value.id) && text(value.key) && text(value.title)
    && nullable(value.updated_at, text) && nullable(value.by, endpoint)
}

function projectBoard(value: unknown): value is ProjectBoard {
  return object(value) && project(value.project) && nullable(value.claude_project, text) && list(value.routes, route)
    && list(value.conversations, conversation) && list(value.history, history) && list(value.memory, note)
}

export const boardQuery = queryOptions({
  queryKey: ["board"],
  queryFn: ({ signal }) => requestJson("/api/board", board, { signal }),
  staleTime: 15_000,
})

export function projectBoardQuery(slug: string) {
  return queryOptions({
    queryKey: ["board", "project", slug],
    queryFn: ({ signal }) => requestJson(`/api/board/projects/${encodeURIComponent(slug)}`, projectBoard, { signal }),
    staleTime: 15_000,
    retry: false,
  })
}

export function coordinatorQuery(title: string | null) {
  const search = title ? `?${new URLSearchParams({ claude_project: title })}` : ""
  return queryOptions({
    queryKey: ["prompt", "coordinator", title],
    queryFn: ({ signal }) => requestJson(`/api/prompts/coordinator${search}`, (value): value is { text: string } =>
      object(value) && text(value.text), { signal }),
  })
}

export function formatDay(at: string) {
  return new Date(at).toLocaleDateString(undefined, { month: "short", day: "numeric" })
}

export function formatTime(at: string) {
  return `${formatDay(at)} ${new Date(at).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" })}`
}

export function formatAge(at: string) {
  const minutes = Math.max(0, Math.floor((Date.now() - new Date(at).getTime()) / 60_000))
  if (minutes < 1) return "now"
  if (minutes < 60) return `${minutes} min`
  if (minutes < 1440) return `${Math.floor(minutes / 60)} h`
  return `${Math.floor(minutes / 1440)} d`
}

export function quantity(n: number, unit: string) {
  return `${n} ${unit}${n === 1 ? "" : "s"}`
}
