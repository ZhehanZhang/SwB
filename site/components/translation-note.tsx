import type { Locale } from '@/lib/i18n';
import { releaseBranch, repository } from '@/lib/urls';

const notes: Partial<Record<Locale, { text: string; guide: string; issues: string }>> = {
  es: {
    text: 'Esta página es una traducción del inglés hecha y revisada por agentes. Zhehan lee inglés y chino. Tu ayuda con la revisión es bienvenida.',
    guide: 'Ayuda a revisar',
    issues: 'Informa de un error de traducción',
  },
  fr: {
    text: "Des agents ont traduit cette page depuis la version anglaise, puis l'ont vérifiée. Zhehan lit l'anglais et le chinois. Votre aide à la relecture est la bienvenue.",
    guide: 'Aider à relire',
    issues: 'Signaler un problème de traduction',
  },
  de: {
    text: 'Agenten haben diese Seite aus dem Englischen übersetzt und geprüft. Zhehan liest Englisch und Chinesisch. Hilfe beim Korrekturlesen ist herzlich willkommen.',
    guide: 'Beim Korrekturlesen helfen',
    issues: 'Übersetzungsfehler melden',
  },
  ko: {
    text: '이 페이지는 에이전트가 영어 원문을 번역하고 검토했습니다. Zhehan은 영어와 중국어를 읽습니다. 교정을 도와주시면 감사하겠습니다.',
    guide: '교정 돕기',
    issues: '번역 문제 신고',
  },
  ja: {
    text: 'このページは、エージェントが英語版から翻訳し、確認しました。Zhehan が読めるのは英語と中国語です。校正へのご協力をお待ちしています。',
    guide: '校正に協力する',
    issues: '翻訳の問題を報告する',
  },
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
