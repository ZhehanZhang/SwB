import type { Locale } from './i18n';

export const setupPickerCopy = {
  en: { setup: 'Set up', apps: 'Apps', prompt: 'Setup prompt', copy: 'Copy prompt', copied: 'Copied', copyError: 'Select the prompt to copy it' },
  'zh-Hans': { setup: '设置', apps: '应用', prompt: '设置提示词', copy: '复制提示词', copied: '已复制', copyError: '请选中提示词后复制' },
  'zh-Hant': { setup: '設定', apps: '應用程式', prompt: '設定提示詞', copy: '複製提示詞', copied: '已複製', copyError: '請選取提示詞後複製' },
  es: {
    "setup": "Configurar",
    "apps": "Apps",
    "prompt": "Prompt de configuración",
    "copy": "Copiar prompt",
    "copied": "Copiado",
    "copyError": "Selecciona el prompt para copiarlo"
  },
  fr: {
    "setup": "Configurer",
    "apps": "Applications",
    "prompt": "Prompt de configuration",
    "copy": "Copier le prompt",
    "copied": "Copié",
    "copyError": "Sélectionnez le prompt pour le copier"
  },
  de: {
    "setup": "Einrichten",
    "apps": "Apps",
    "prompt": "Einrichtungsprompt",
    "copy": "Prompt kopieren",
    "copied": "Kopiert",
    "copyError": "Markieren Sie den Prompt, um ihn zu kopieren"
  },
  ko: {
    "setup": "설정",
    "apps": "앱",
    "prompt": "설정 프롬프트",
    "copy": "프롬프트 복사",
    "copied": "복사됨",
    "copyError": "프롬프트를 선택해 복사하세요"
  },
  ja: {
    "setup": "セットアップ",
    "apps": "アプリ",
    "prompt": "セットアップ用プロンプト",
    "copy": "プロンプトをコピー",
    "copied": "コピーしました",
    "copyError": "プロンプトを選択してコピーしてください"
  },
} satisfies Record<Locale, Record<string, string>>;
