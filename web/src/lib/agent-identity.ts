import { connectorKind } from "./connector-kind.js"

const clientNames: Record<string, string> = {
  claude: "Claude", "claude-code": "Claude Code", codex: "Codex", chatgpt: "ChatGPT", gemini: "Gemini", "gemini-cli": "Gemini CLI",
  grok: "Grok", xai: "xAI",
}

export const principalLabel = (value: string) => Object.hasOwn(clientNames, value) ? clientNames[value] : value

export const agentLogo = (value: string) => connectorKind(value) ?? (Object.hasOwn(clientNames, value) ? value : "mcp")
