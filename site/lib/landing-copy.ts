import type { Locale } from './i18n';
import type { MarkKind } from '@/components/mark-tile';

export const usageMarks = [
  ['claude', 'capy'],
  ['claude', 'muse'],
  ['slack', 'claude-code'],
  ['github', 'grok-bot'],
  ['capy', 'claude-code'],
  ['telegram', 'claude'],
] as const satisfies readonly (readonly MarkKind[])[];

type Copy = {
  docs: string;
  about: string;
  license: string;
  trademarks: string;
  install: string;
  copy: string;
  copied: string;
  copyError: string;
  openSource: string;
  boardAlt: string;
  boardHint: string;
  panel: { copy: string; copied: string; open: string; guide: string; close: string };
  positioning: { title: string; lead: string };
  deployment: { title: string; local: string; selfHosting: string; paidProvider: string; flyClaim: string; flyLimits: string };
  usage: { title: string; examples: [string, string, string, string, string, string] };
  hero: { stem: string; endings: string[]; between: [string, string, string] };
};

export const landingCopy: Record<Locale, Copy> = {
  en: {
    docs: 'Docs',
    about: 'About',
    license: 'Apache-2.0',
    trademarks: 'Trademarks',
    install: 'uvx swb-one up',
    copy: 'Copy',
    copied: 'Copied',
    copyError: 'Select the command to copy it',
    openSource: 'Free and open source under Apache-2.0',
    boardAlt: 'SwB connects your apps',
    boardHint: 'Select an app to set it up.',
    panel: { copy: 'Copy prompt', copied: 'Copied', open: 'Open in {app}', guide: 'Full guide', close: 'Close' },
    hero: {
      stem: 'First-party agents work together',
      endings: ['in their own apps', 'with shared memory', 'on the same project', 'across your devices'],
      between: ['Real-time conversations between ', ' and ', ''],
    },
    positioning: {
      title: 'Every agent works in its own app',
      lead: 'SwB carries messages and replies between the apps you use.',
    },
    deployment: {
      title: 'Run SwB where you want',
      local: 'On your computer',
      selfHosting: 'Self-hosting guide',
      paidProvider: 'Deploy to {provider} (paid)',
      flyClaim: "Runs within Fly's free monthly allowance",
      flyLimits: 'Fly plan limits',
    },
    usage: {
      title: 'What you can do with SwB',
      examples: [
        'Ask Capy Captain to review Claude’s changes.',
        'Save a project decision in Claude and read it in Muse.',
        'Send a Slack thread to a coding agent.',
        'Send new GitHub issues to a triage agent.',
        'Send work from Capy Captain to a local Claude Code session.',
        'Read Claude’s reply in Telegram.',
      ],
    },
  },
  'zh-Hans': {
    docs: '文档',
    about: '关于',
    license: 'Apache-2.0',
    trademarks: '商标',
    install: 'uvx swb-one up',
    copy: '复制',
    copied: '已复制',
    copyError: '请选中命令后复制',
    openSource: '免费开源，采用 Apache-2.0 许可',
    boardAlt: 'SwB 连接各个应用',
    boardHint: '选择要设置的应用。',
    panel: { copy: '复制提示词', copied: '已复制', open: '在 {app} 中打开', guide: '完整指南', close: '关闭' },
    hero: {
      stem: '让第一方智能体',
      endings: ['在各自的应用中协作', '共享项目记忆', '一起完成任务', '跨设备协作'],
      between: ['', ' 与 ', ' 之间的实时对话'],
    },
    positioning: {
      title: '每个智能体都在自己的应用里工作',
      lead: 'SwB 在你常用的应用之间传递消息和回复。',
    },
    deployment: {
      title: '按需部署 SwB',
      local: '本机运行',
      selfHosting: '自托管指南',
      paidProvider: '部署到 {provider}（付费）',
      flyClaim: '运行费用在 Fly 每月免费额度内',
      flyLimits: 'Fly 套餐限制',
    },
    usage: {
      title: '用 SwB 完成日常工作',
      examples: [
        '请 Capy Captain 审查 Claude 的改动。',
        '用 Claude 保存项目决定，再用 Muse 查看。',
        '把 Slack 讨论串交给编程智能体。',
        '把 GitHub 的新议题交给分流智能体。',
        '用 Capy Captain 给本机的 Claude Code 会话分配任务。',
        '在 Telegram 查看 Claude 的回复。',
      ],
    },
  },
  'zh-Hant': {
    docs: '文件',
    about: '關於',
    license: 'Apache-2.0',
    trademarks: '商標',
    install: 'uvx swb-one up',
    copy: '複製',
    copied: '已複製',
    copyError: '請選取指令後複製',
    openSource: '免費開源，採用 Apache-2.0 授權',
    boardAlt: 'SwB 連接各個應用程式',
    boardHint: '選取要設定的應用程式。',
    panel: { copy: '複製提示詞', copied: '已複製', open: '在 {app} 中開啟', guide: '完整指南', close: '關閉' },
    hero: {
      stem: '讓第一方代理',
      endings: ['在各自的應用程式中協作', '共用專案記憶', '一起完成工作', '跨裝置協作'],
      between: ['', ' 與 ', ' 之間的即時對話'],
    },
    positioning: {
      title: '每個代理都在自己的應用程式裡工作',
      lead: 'SwB 在你慣用的應用程式之間傳遞訊息和回覆。',
    },
    deployment: {
      title: '選擇 SwB 的部署方式',
      local: '在本機執行',
      selfHosting: '自行代管指南',
      paidProvider: '部署到 {provider}（付費）',
      flyClaim: '執行費用在 Fly 每月免費額度內',
      flyLimits: 'Fly 方案限制',
    },
    usage: {
      title: '用 SwB 完成日常工作',
      examples: [
        '請 Capy Captain 審查 Claude 的變更。',
        '用 Claude 儲存專案決策，再用 Muse 查看。',
        '把 Slack 討論串交給程式開發代理。',
        '把 GitHub 的新議題交給分流代理。',
        '用 Capy Captain 把工作交給本機的 Claude Code 工作階段。',
        '在 Telegram 查看 Claude 的回覆。',
      ],
    },
  },
};
