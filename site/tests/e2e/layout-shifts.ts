type LayoutShift = PerformanceEntry & {
  hadRecentInput: boolean;
  value: number;
  sources: {
    node: Node | null;
    previousRect: DOMRectReadOnly;
    currentRect: DOMRectReadOnly;
  }[];
};

type Attribution = {
  value: number;
  time: number;
  fonts: string[];
  sources: {
    node: string | undefined;
    previousRect: DOMRectInit;
    currentRect: DOMRectInit;
  }[];
};

export function observeLayoutShifts() {
  const metrics = Object.assign(window, { swbLayoutShifts: [] as Attribution[] });
  new PerformanceObserver((list) => {
    for (const entry of list.getEntries() as LayoutShift[]) {
      if (entry.hadRecentInput) continue;
      metrics.swbLayoutShifts.push({
        value: entry.value,
        time: entry.startTime,
        fonts: Array.from(document.fonts, ({ family, status }) => `${family}: ${status}`),
        sources: entry.sources.map(({ node, previousRect, currentRect }) => ({
          node: (node instanceof Element ? node.outerHTML : node?.parentElement?.outerHTML)?.slice(0, 300),
          previousRect: previousRect.toJSON(),
          currentRect: currentRect.toJSON(),
        })),
      });
    }
  }).observe({ type: 'layout-shift', buffered: true });
}

export function readLayoutShifts() {
  return (window as unknown as { swbLayoutShifts: Attribution[] }).swbLayoutShifts;
}
