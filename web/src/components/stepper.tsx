import { Check } from "lucide-react"
import { cn } from "@/lib/utils"

type StepperProps = {
  label: string
  steps: readonly { id: string; label: string }[]
  currentStep: number
}

export function Stepper({ label, steps, currentStep }: StepperProps) {
  return (
    <ol aria-label={label} className="flex flex-col gap-2">
      {steps.map((step, index) => {
        const complete = index < currentStep
        const current = index === currentStep
        return (
          <li
            key={step.id}
            aria-current={current ? "step" : undefined}
            className={cn("flex min-h-9 items-center gap-3 text-sm", current && "font-semibold", !current && "text-muted-foreground")}
          >
            <span
              aria-hidden
              className={cn("grid size-6 shrink-0 place-items-center rounded-sm border font-mono text-xs", current && "border-transparent bg-lamp text-console")}
            >
              {complete ? <Check className="size-3.5 text-ok" /> : index + 1}
            </span>
            {step.label}{complete && <span className="sr-only"> (completed)</span>}
          </li>
        )
      })}
    </ol>
  )
}
