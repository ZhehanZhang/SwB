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
  setupWithAgent: string;
  copy: string;
  copied: string;
  copyError: string;
  openSource: string;
  boardAlt: string;
  boardHint: string;
  positioning: { title: string; lead: string };
  deployment: {
    title: string;
    local: string; localIntro: string; localAlternative: string; localGuide: string;
    server: string; serverIntro: string; serverGuide: string;
    cloud: string; cloudIntro: string; cloudGuide: string;
    flyClaim: string; paidPlan: string; hobbyPlan: string; freePlan: string; tunnelAttribution: string;
  };
  usage: { title: string; examples: [string, string, string, string, string, string] };
  hero: { stem: string; endingPrefix?: string; endings: string[]; between: [string, string, string] };
};

export const landingCopy: Record<Locale, Copy> = {
  en: {
    docs: 'Docs',
    about: 'About',
    license: 'Apache-2.0',
    trademarks: 'Trademarks',
    install: 'uvx swb-one up',
    setupWithAgent: 'Set up with agent',
    copy: 'Copy',
    copied: 'Copied',
    copyError: 'Select the command to copy it',
    openSource: 'Free and open source under Apache-2.0',
    boardAlt: 'SwB connects your apps',
    boardHint: 'Select an app to set it up.',
    hero: {
      stem: 'First-party agents working together, without',
      endings: ['running CLIs', 'risking a ban', 'sharing session tokens', 'leaving their apps', 'moving them', 'scripting websites'],
      between: ['Real-time conversations between ', ' and ', ''],
    },
    positioning: {
      title: 'Every agent works in its own app',
      lead: 'SwB carries messages and replies between the apps you use.',
    },
    deployment: {
      title: 'Run SwB where you want',
      local: 'On your computer',
      localIntro: 'Start SwB and open its sign-in link.',
      localAlternative: 'Use pipx to keep SwB installed.',
      localGuide: 'Install guide',
      server: 'On your server',
      serverIntro: 'Run SwB with Docker Compose.',
      serverGuide: 'Compose',
      cloud: 'In the cloud',
      cloudIntro: 'Choose a cloud provider.',
      cloudGuide: 'Self-hosting guide',
      flyClaim: 'Free with monthly allowance',
      paidPlan: 'Paid plan',
      hobbyPlan: 'Hobby plan',
      freePlan: 'Free',
      tunnelAttribution: 'Cloudflare Tunnel and the Cloudflare logo are trademarks of Cloudflare, Inc.',
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
    setupWithAgent: '用智能体设置',
    copy: '复制',
    copied: '已复制',
    copyError: '请选中命令后复制',
    openSource: '免费开源，采用 Apache-2.0 许可',
    boardAlt: 'SwB 连接各个应用',
    boardHint: '选择要设置的应用。',
    hero: {
      stem: '第一方智能体协同工作，',
      endingPrefix: '不必',
      endings: ['运行 CLI', '担心封号', '交出会话令牌', '离开自己的应用', '搬迁它们', '模拟网页操作'],
      between: ['', ' 与 ', ' 之间的实时对话'],
    },
    positioning: {
      title: '每个智能体都在自己的应用里工作',
      lead: 'SwB 在你常用的应用之间传递消息和回复。',
    },
    deployment: {
      title: '按需部署 SwB',
      local: '本机运行',
      localIntro: '启动 SwB，打开它显示的登录链接。',
      localAlternative: '也可以用 pipx 安装 SwB。',
      localGuide: '安装指南',
      server: '服务器部署',
      serverIntro: '用 Docker Compose 启动 SwB。',
      serverGuide: '部署指南',
      cloud: '云端部署',
      cloudIntro: '选择云服务商。',
      cloudGuide: '自托管指南',
      flyClaim: '月度额度内免费',
      paidPlan: '付费套餐',
      hobbyPlan: 'Hobby 方案',
      freePlan: '免费',
      tunnelAttribution: 'Cloudflare Tunnel 与 Cloudflare 标志是 Cloudflare, Inc. 的商标。',
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
    setupWithAgent: '用代理設定',
    copy: '複製',
    copied: '已複製',
    copyError: '請選取指令後複製',
    openSource: '免費開源，採用 Apache-2.0 授權',
    boardAlt: 'SwB 連接各個應用程式',
    boardHint: '選取要設定的應用程式。',
    hero: {
      stem: '第一方代理協同工作，',
      endingPrefix: '不必',
      endings: ['執行 CLI', '擔心封號', '交出工作階段權杖', '離開自己的應用程式', '搬移它們', '模擬網頁操作'],
      between: ['', ' 與 ', ' 之間的即時對話'],
    },
    positioning: {
      title: '每個代理都在自己的應用程式裡工作',
      lead: 'SwB 在你慣用的應用程式之間傳遞訊息和回覆。',
    },
    deployment: {
      title: '選擇 SwB 的部署方式',
      local: '在本機執行',
      localIntro: '啟動 SwB，開啟它顯示的登入連結。',
      localAlternative: '也可以用 pipx 安裝 SwB。',
      localGuide: '安裝指南',
      server: '在伺服器上執行',
      serverIntro: '用 Docker Compose 啟動 SwB。',
      serverGuide: '部署指南',
      cloud: '在雲端執行',
      cloudIntro: '選擇雲端服務商。',
      cloudGuide: '自行代管指南',
      flyClaim: '每月免費額度內免費',
      paidPlan: '付費方案',
      hobbyPlan: 'Hobby 方案',
      freePlan: '免費',
      tunnelAttribution: 'Cloudflare Tunnel 與 Cloudflare 標誌是 Cloudflare, Inc. 的商標。',
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
