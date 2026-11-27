import type { Locale } from './i18n';

export const setupPickerCopy = {
  en: { setup: 'Set up', setupWithAgent: 'Set up with agent', apps: 'Apps', prompt: 'Setup prompt', copy: 'Copy prompt', copied: 'Copied', copyError: 'Select the prompt to copy it', close: 'Close' },
  'zh-Hans': { setup: '设置', setupWithAgent: '用智能体设置', apps: '应用', prompt: '设置提示词', copy: '复制提示词', copied: '已复制', copyError: '请选中提示词后复制', close: '关闭' },
  'zh-Hant': { setup: '設定', setupWithAgent: '用代理設定', apps: '應用程式', prompt: '設定提示詞', copy: '複製提示詞', copied: '已複製', copyError: '請選取提示詞後複製', close: '關閉' },
} satisfies Record<Locale, Record<string, string>>;
