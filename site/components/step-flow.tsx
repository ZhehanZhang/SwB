export function StepFlow({ steps }: { steps: { title: string; description: string }[] }) {
  return (
    <section className="swb-flow relative py-16 sm:py-24">
      <svg className="swb-flow-cord" viewBox="0 0 1000 20" preserveAspectRatio="none" aria-hidden="true">
        <path id="swb-flow-path" d="M10 10 H990" className="swb-cord swb-cord-out" />
        <circle r={6} className="swb-pulse swb-pulse-out">
          <animateMotion dur="6s" repeatCount="indefinite" keyTimes="0;1" keyPoints="0;1" calcMode="linear"><mpath href="#swb-flow-path" /></animateMotion>
        </circle>
      </svg>
      <div className="relative grid gap-10 md:grid-cols-3 md:gap-12">
        {steps.map(({ title, description }) => (
          <div key={title} className="swb-flow-step">
            <span className="swb-flow-jack" aria-hidden="true" />
            <h2 className="mt-6 font-display text-xl font-extrabold">{title}</h2>
            <p className="mt-1.5 text-sm text-fd-muted-foreground">{description}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
