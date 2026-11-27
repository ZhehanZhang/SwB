export function connectorKind(kind?: string | null) {
  switch (kind?.toLowerCase().replaceAll("_", "-")) {
    case "slack": return "slack"
    case "discord": return "discord"
    case "telegram": return "telegram"
    case "github": return "github"
    case "webhook": return "webhook"
    case "chatgpt":
    case "openai": return "chatgpt"
    case "dot":
    case "dots": return "dots"
    case "muse":
    case "muse-code": return "muse"
    default: return undefined
  }
}
