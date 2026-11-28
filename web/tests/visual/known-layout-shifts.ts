const agentSheet: Readonly<Record<number, number>> = { 768: 0.59, 1024: 0.69, 1440: 0.55, 1920: 0.43 }

const layoutPassB: Readonly<Record<string, Readonly<Record<number, number>>>> = {
  "busy/about": { 375: 0.43, 768: 0.35 },
  "busy/agents": { 375: 0.15, 768: 0.15, 1024: 0.16, 1440: 0.095, 1920: 0.075 },
  "busy/agent-routine": agentSheet,
  "busy/agent-grok-routine": agentSheet,
  "busy/agent-captain": agentSheet,
  "busy/agent-dot": agentSheet,
  "busy/agent-key": agentSheet,
  "busy/agent-mailbox": agentSheet,
  "busy/connections-discord": { 375: 0.38, 768: 0.18, 1024: 0.17, 1440: 0.115 },
  "busy/connections-slack": { 375: 0.38, 768: 0.18, 1024: 0.17, 1440: 0.115 },
  "busy/connections-telegram": { 375: 0.23, 768: 0.10, 1024: 0.09 },
  "busy/inbox": { 375: 0.26, 768: 0.16, 1024: 0.17, 1440: 0.17, 1920: 0.15 },
  "busy/inbox-project": { 375: 0.26, 768: 0.16, 1024: 0.17, 1440: 0.17, 1920: 0.15 },
  "busy/memory": { 375: 0.27 },
  "busy/memory-agent": { 375: 0.27 },
  "busy/memory-project": { 375: 0.27 },
  "busy/memory-backup": { 375: 0.49, 768: 0.08, 1024: 0.07 },
  "busy/settings": { 375: 0.11, 768: 0.12, 1024: 0.1 },
  "busy/tokens": { 375: 0.69, 768: 0.66, 1024: 0.69, 1440: 0.52, 1920: 0.41 },
  "empty/about": { 375: 0.43, 768: 0.35 },
  "empty/settings": { 375: 0.11, 768: 0.1, 1024: 0.09 },
  "error/agents": { 375: 0.12 },
}

export function layoutShiftBudget(seed: string, route: string, width: number): number {
  return layoutPassB[`${seed}/${route}`]?.[width] ?? 0.05
}
