import type { Locale } from '@/lib/i18n';
import { releaseBranch, repository } from '@/lib/urls';

const notes: Partial<Record<Locale, { text: string; guide: string; issues: string }>> = {
};

export function TranslationNote({ lang }: { lang: string }) {
  const note = notes[lang as Locale];
  if (!note) return null;
  return (
    <p data-translation-note className="mt-5 text-sm text-fd-muted-foreground">
      {note.text}{' '}
      <a className="underline underline-offset-4 hover:text-fd-foreground" href={`${repository}/blob/${releaseBranch}/CONTRIBUTING.md#proofread-translations`}>{note.guide}</a>{' '}
      <a className="underline underline-offset-4 hover:text-fd-foreground" href={`${repository}/issues/new?labels=translation&title=${encodeURIComponent(`[${lang}] `)}`}>{note.issues}</a>
    </p>
  );
}
