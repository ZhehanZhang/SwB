export function Brand({ name }: { name: string }) {
  return (
    <span className="inline-flex items-center gap-2.5 font-display text-lg font-extrabold tracking-tight">
      <img className="swb-mark size-7" src="/swb.svg" width={28} height={28} alt="" />
      {name}
    </span>
  );
}
