import type { MarkKind } from '@/components/mark-tile';
import { localize, type Locale } from './i18n';

export type SetupMark = Exclude<MarkKind, 'swb'>;

export function getStartedHref(lang: Locale, marks: readonly SetupMark[] = []) {
  const path = localize(lang, '/docs/get-started');
  const agents = new Set(marks.map((mark) => mark === 'chatgpt' || mark === 'dots' ? 'chatgpt-dots' : mark));
  return agents.size ? `${path}?agents=${[...agents].join(',')}` : path;
}
