import { Lamp } from "@/components/console/lamp"
import type { ConnectionState } from "@/lib/connections"

const labels = {
  connected: "Connected",
  disconnected: "Disconnected",
  not_configured: "Not configured",
  loading: "Loading…",
  unavailable: "Unavailable",
}

export function ConnectionStatus({ state }: { state: ConnectionState | "loading" | "unavailable" }) {
  return (
    <span className="inline-flex items-center gap-2 text-body-sm text-muted-foreground">
      <Lamp state={state === "connected" ? "on" : "off"} />
      {labels[state]}
    </span>
  )
}
