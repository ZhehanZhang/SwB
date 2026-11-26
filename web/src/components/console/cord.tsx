export type CordKind = "source" | "agent" | "sink"
type Point = { x: number; y: number }

type CordProps = {
  from: Point
  to: Point
  kind: CordKind
  axis?: "horizontal" | "vertical"
  state?: "idle" | "selected" | "dragging"
} & (
  | { onSelect?: undefined; label?: never }
  | { onSelect: () => void; label: string }
)

export function Cord({ from, to, kind, axis = "horizontal", state = "idle", onSelect, label }: CordProps) {
  const vertical = axis === "vertical"
  const distance = vertical ? to.y - from.y : to.x - from.x
  const offset = Math.max(24, Math.abs(distance) * 0.45)
  const dx = vertical ? 0 : offset
  const dy = vertical ? offset : 0
  const path = `M ${from.x} ${from.y} C ${from.x + dx} ${from.y + dy}, ${to.x - dx} ${to.y - dy}, ${to.x} ${to.y}`
  const dragging = state === "dragging"
  const stroke = dragging ? "var(--lamp)" : `var(--cord-${kind})`
  const selectable = Boolean(onSelect) && !dragging

  return (
    <g
      aria-hidden={selectable ? undefined : true}
      aria-label={selectable ? label : undefined}
      aria-pressed={selectable ? state === "selected" : undefined}
      className={selectable ? "group cursor-pointer outline-none" : undefined}
      role={selectable ? "button" : undefined}
      tabIndex={selectable ? 0 : undefined}
      onClick={selectable ? onSelect : undefined}
      onKeyDown={selectable ? (event) => {
        if (event.key !== "Enter" && event.key !== " ") return
        event.preventDefault()
        onSelect?.()
      } : undefined}
    >
      {(state === "selected" || selectable) && (
        <path
          className={state === "selected" ? "group-focus-visible:[stroke-width:12]" : "opacity-0 group-focus-visible:opacity-100"}
          d={path}
          fill="none"
          stroke="var(--lamp)"
          strokeWidth={8}
          vectorEffect="non-scaling-stroke"
          pointerEvents="none"
        />
      )}
      <path
        d={path}
        fill="none"
        stroke={stroke}
        strokeWidth={4}
        strokeLinecap="round"
        strokeDasharray={dragging ? "1 8" : undefined}
        vectorEffect="non-scaling-stroke"
        pointerEvents="none"
      />
      {dragging && <circle cx={to.x} cy={to.y} r={5} fill="var(--lamp)" pointerEvents="none" />}
      {selectable && (
        <path d={path} fill="none" stroke="transparent" strokeWidth={20} vectorEffect="non-scaling-stroke" pointerEvents="stroke" />
      )}
    </g>
  )
}
