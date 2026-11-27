import { readPrompt } from '@/lib/prompts';
import { PromptActions } from './prompt-actions';

const labels = {
  en: { copy: 'Copy prompt', copied: 'Copied', claude: 'Open in Claude', chatgpt: 'Open in ChatGPT' },
  'zh-Hans': { copy: '复制提示词', copied: '已复制', claude: '在 Claude 中打开', chatgpt: '在 ChatGPT 中打开' },
  'zh-Hant': { copy: '複製提示詞', copied: '已複製', claude: '在 Claude 中開啟', chatgpt: '在 ChatGPT 中開啟' },
  es: { copy: 'Copiar prompt', copied: 'Copiado', claude: 'Abrir en Claude', chatgpt: 'Abrir en ChatGPT' },
  fr: { copy: 'Copier le prompt', copied: 'Copié', claude: 'Ouvrir dans Claude', chatgpt: 'Ouvrir dans ChatGPT' },
  de: { copy: 'Prompt kopieren', copied: 'Kopiert', claude: 'In Claude öffnen', chatgpt: 'In ChatGPT öffnen' },
  ko: { copy: '프롬프트 복사', copied: '복사했습니다', claude: 'Claude에서 열기', chatgpt: 'ChatGPT에서 열기' },
  ja: { copy: 'プロンプトをコピー', copied: 'コピーしました', claude: 'Claude で開く', chatgpt: 'ChatGPT で開く' },
};

export function SetupPrompt({ name, lang = 'en' }: { name: string; lang?: keyof typeof labels }) {
  const text = readPrompt(name);
  return (
    <figure className="swb-prompt not-prose my-6 overflow-hidden rounded-lg border bg-fd-card">
      <PromptActions text={text} labels={labels[lang]} showOpenLinks={name !== 'muse'} />
      <pre className="max-h-[28rem] overflow-auto whitespace-pre-wrap border-t px-4 py-3.5 font-mono text-[13px] leading-relaxed">{text}</pre>
    </figure>
  );
}
