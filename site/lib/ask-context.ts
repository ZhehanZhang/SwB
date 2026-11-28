import type { AskPage } from './docs-assets';

export function askContext(pages: AskPage[], question: string, location: string | undefined, budget: number) {
  const terms = [...new Set(question.toLowerCase().match(/[a-z0-9_.-]{3,}|[\u3400-\u9fff]{2}/g) ?? [])];
  const ranked = pages.map(({ url, root, text }) => {
    const lower = text.toLowerCase();
    const hits = terms.reduce((sum, term) => sum + Math.min(lower.split(term).length - 1, 20), 0);
    return { text, score: hits + (url === location ? 1000 : 0) + (root ? 5 : 0) };
  });
  const picked: string[] = [];
  let used = 0;
  for (const { text } of ranked.sort((a, b) => b.score - a.score)) {
    const part = text.slice(0, budget - used);
    if (part.length < 200) break;
    picked.push(part);
    used += part.length;
  }
  return picked.join('\n\n---\n\n');
}
