export function displayReference(value: string, fallback = "Capy thread"): string {
  const named = /^(?:captain|thread) (.+) \(jam_[\w-]+\)$/i.exec(value)
  return (named?.[1] ?? value)
    .replace(/\s+\(jam_[\w-]+\)/gi, "")
    .replace(/\b(?:(?:[\w-]+:)?(captain|thread):|(captain|thread) )?jam_[\w-]+/gi,
      (_match, addressKind?: string, describedKind?: string) => {
        const kind = (addressKind ?? describedKind)?.toLowerCase()
        return kind === "captain" ? "Capy Captain" : kind === "thread" ? "Capy thread" : fallback
      })
}
