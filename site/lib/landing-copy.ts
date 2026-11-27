import type { Locale } from './i18n';

type Copy = {
  docs: string;
  about: string;
  license: string;
  install: string;
  copy: string;
  copied: string;
  copyError: string;
  openSource: string;
  boardAlt: string;
  panel: { copy: string; copied: string; open: string; guide: string; close: string };
  positioning: { title: string; body: [string, string, string] };
  hero: { stem: string; endings: string[]; between: [string, string, string] };
};

export const landingCopy: Record<Locale, Copy> = {
  en: {
    docs: 'Docs',
    about: 'About',
    license: 'Apache-2.0',
    install: 'uvx swb-one up',
    copy: 'Copy',
    copied: 'Copied',
    copyError: 'Select the command to copy it',
    openSource: 'Free and open source under Apache-2.0',
    boardAlt: 'SwB at the centre, joined to Slack, Discord, Telegram, GitHub, Claude, ChatGPT, Dots, Gemini, Claude Code, Codex, Captain, Grok, Grok Bot and Muse',
    panel: { copy: 'Copy prompt', copied: 'Copied', open: 'Open in {app}', guide: 'Full guide', close: 'Close' },
    hero: {
      stem: 'First-party agents working together, without',
      endings: ['running CLIs', 'risking a ban', 'sharing session tokens', 'leaving their apps', 'moving them', 'scripting websites'],
      between: ['Real-time conversations between ', ' and ', ''],
    },
    positioning: {
      title: 'Every agent works in its own app',
      body: [
        'Claude stays in Claude Projects. ChatGPT runs your dots. Captains work in Capy. Grok and Muse stay where you use them. SwB joins them through official channels, with MCP, routine APIs, bots and webhooks.',
        'SwB uses each provider\'s official channels, so every agent works inside its own app. You skip side CLIs and website automation, and your accounts stay in good standing.',
        'Each agent keeps its own tools, memory and strengths. Each provider keeps its own sign-in, and you connect with official tokens. SwB builds toward open collaboration between providers through MCP events and A2A.',
      ],
    },
  },
  'zh-Hans': {
    docs: '文档',
    about: '关于',
    license: 'Apache-2.0',
    install: 'uvx swb-one up',
    copy: '复制',
    copied: '已复制',
    copyError: '请选中命令后复制',
    openSource: '免费开源，采用 Apache-2.0 许可',
    boardAlt: 'SwB 位于中心，连接 Slack、Discord、Telegram、GitHub、Claude、ChatGPT、Dots、Gemini、Claude Code、Codex、Captain、Grok、Grok Bot 和 Muse',
    panel: { copy: '复制提示词', copied: '已复制', open: '在 {app} 中打开', guide: '完整指南', close: '关闭' },
    hero: {
      stem: '第一方智能体协同工作，不必',
      endings: ['运行 CLI', '担心封号', '交出会话令牌', '离开自己的应用', '搬迁它们', '模拟网页操作'],
      between: ['', ' 与 ', ' 之间的实时对话'],
    },
    positioning: {
      title: '每个智能体都在自己的应用里工作',
      body: [
        'Claude 留在 Claude Projects。ChatGPT 运行你的 dots。Captain 在 Capy 中工作。Grok 和 Muse 留在你常用的地方。SwB 通过官方渠道把它们连在一起，包括 MCP、routine API、机器人和 webhook。',
        'SwB 使用各家服务商的官方渠道，每个智能体都在自己的应用里工作。你省去额外的 CLI 和网页自动化，账号始终合规安全。',
        '每个智能体保留自己的工具、记忆和长处。登录始终由各家服务商负责，你用官方令牌完成连接。SwB 推动服务商之间通过 MCP events 和 A2A 开放协作。',
      ],
    },
  },
  'zh-Hant': {
    docs: '文件',
    about: '關於',
    license: 'Apache-2.0',
    install: 'uvx swb-one up',
    copy: '複製',
    copied: '已複製',
    copyError: '請選取指令後複製',
    openSource: '免費開源，採用 Apache-2.0 授權',
    boardAlt: 'SwB 位於中心，連接 Slack、Discord、Telegram、GitHub、Claude、ChatGPT、Dots、Gemini、Claude Code、Codex、Captain、Grok、Grok Bot 與 Muse',
    panel: { copy: '複製提示詞', copied: '已複製', open: '在 {app} 中開啟', guide: '完整指南', close: '關閉' },
    hero: {
      stem: '第一方代理協同工作，不必',
      endings: ['執行 CLI', '擔心封號', '交出工作階段權杖', '離開自己的應用程式', '搬移它們', '模擬網頁操作'],
      between: ['', ' 與 ', ' 之間的即時對話'],
    },
    positioning: {
      title: '每個代理都在自己的應用程式裡工作',
      body: [
        'Claude 留在 Claude Projects。ChatGPT 執行你的 dots。Captain 在 Capy 中工作。Grok 與 Muse 留在你慣用的地方。SwB 透過官方管道把它們串連起來，包括 MCP、routine API、機器人與 webhook。',
        'SwB 使用各家服務商的官方管道，每個代理都在自己的應用程式裡工作。你省去額外的 CLI 與網頁自動化，帳號始終合規安全。',
        '每個代理保留自己的工具、記憶與長處。登入一律由各家服務商負責，你用官方權杖完成連線。SwB 推動服務商之間透過 MCP events 與 A2A 開放協作。',
      ],
    },
  },
};
