import { Check } from "lucide-react"
import { cn } from "@/lib/utils"

type StepProps = { number: number; title: string; detail?: string; state: "done" | "current" | "todo" }

export function Step({ number, title, detail, state }: StepProps) {
  return (
    <li aria-current={state === "current" ? "step" : undefined} className="flex min-h-10 items-center gap-3">
      <span aria-hidden className={cn("grid size-6 shrink-0 place-items-center rounded-full bg-muted text-label-sm text-muted-foreground", state === "done" && "bg-primary text-primary-foreground", state === "current" && "bg-accent text-primary-foreground")}>
        {state === "done" ? <Check className="size-4" /> : number}
      </span>
      <span>
        <span className={cn("block text-label-sm", state === "todo" && "text-muted-foreground")}>{title}</span>
        {detail && <span className="block text-body-sm text-muted-foreground">{detail}</span>}
        {state === "done" && <span className="sr-only"> completed</span>}
      </span>
    </li>
  )
}

export function Stepper({ label, steps, currentStep }: { label: string; steps: readonly { id: string; label: string; detail?: string }[]; currentStep: number }) {
  return <ol aria-label={label} className="grid gap-2">{steps.map((step, index) => (
    <Step key={step.id} number={index + 1} title={step.label} detail={step.detail} state={index < currentStep ? "done" : index === currentStep ? "current" : "todo"} />
  ))}</ol>
}
