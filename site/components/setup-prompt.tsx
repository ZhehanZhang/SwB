import { readPrompt } from '@/lib/prompts';
import { SetupPromptContent } from './prompt-actions';

const labels = {
  en: { copy: 'Copy prompt', copied: 'Copied', open: 'Open in {app}' },
  'zh-Hans': { copy: '复制提示词', copied: '已复制', open: '在 {app} 中打开' },
  'zh-Hant': { copy: '複製提示詞', copied: '已複製', open: '在 {app} 中開啟' },
  es: { copy: 'Copiar prompt', copied: 'Copiado', open: 'Abrir en {app}' },
  fr: { copy: 'Copier le prompt', copied: 'Copié', open: 'Ouvrir dans {app}' },
  de: { copy: 'Prompt kopieren', copied: 'Kopiert', open: 'In {app} öffnen' },
  ko: { copy: '프롬프트 복사', copied: '복사했습니다', open: '{app}에서 열기' },
  ja: { copy: 'プロンプトをコピー', copied: 'コピーしました', open: '{app} で開く' },
};

export function SetupPrompt({ name, lang = 'en' }: { name: string; lang?: keyof typeof labels }) {
  const text = readPrompt(name);
  return (
    <figure className="swb-prompt not-prose my-6 overflow-hidden rounded-lg border bg-fd-card">
      <SetupPromptContent text={text} labels={labels[lang]} targets={name === 'muse' ? [] : ['claude', 'chatgpt']} />
    </figure>
  );
}
