import type { Locale } from './i18n';

export const setupPickerCopy = {
  en: { setup: 'Set up', apps: 'Apps', prompt: 'Setup prompt', copy: 'Copy prompt', copied: 'Copied', copyError: 'Select the prompt to copy it' },
  'zh-Hans': { setup: '设置', apps: '应用', prompt: '设置提示词', copy: '复制提示词', copied: '已复制', copyError: '请手动选中并复制提示词' },
  'zh-Hant': { setup: '設定', apps: '應用程式', prompt: '設定提示詞', copy: '複製提示詞', copied: '已複製', copyError: '請手動選取並複製提示詞' },
} satisfies Record<Locale, Record<string, string>>;
