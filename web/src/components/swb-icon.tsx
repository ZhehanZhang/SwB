import roundel from "@/assets/swb-icon.svg"
import { cn } from "@/lib/utils"

export function SwbIcon({ className, muted = false }: { className?: string; muted?: boolean }) {
  return <img aria-hidden alt="" src={roundel} className={cn("size-12 shrink-0", muted && "opacity-85", className)} />
}
