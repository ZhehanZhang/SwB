import type { Locale } from './i18n';

export const exchangeCopy = {
  en: { you: 'You' },
  'zh-Hans': { you: '你' },
  'zh-Hant': { you: '你' },
} satisfies Record<Locale, { you: string }>;

export const agentNames = {
  claude: 'Claude',
  'claude-code': 'Claude Code',
  'grok-bot': 'Grok Bot',
  capy: 'Capy Captain',
  muse: 'Muse',
  slack: 'Slack #launch',
  github: 'GitHub',
};

type Message = [speaker: keyof typeof agentNames | 'you', text: Record<Locale, string>];
export type ExchangeScene = { app: keyof typeof agentNames; messages: Message[] };

export const exchangeScenes: ExchangeScene[] = [
  {
    app: 'claude',
    messages: [
      ['claude', {
        en: 'Grok Bot, check the replies to today’s release post.',
        'zh-Hans': 'Grok Bot，看看今天发布公告下的回复。',
        'zh-Hant': 'Grok Bot，看看今天版本公告下的留言。',
      }],
      ['grok-bot', {
        en: 'Readers asked how to start SwB with Docker.',
        'zh-Hans': '有读者问怎么用 Docker 启动 SwB。',
        'zh-Hant': '有讀者想知道怎麼用 Docker 啟動 SwB。',
      }],
      ['claude', {
        en: 'I will add the Docker command to the release notes.',
        'zh-Hans': '我把 Docker 启动命令补到发布说明里。',
        'zh-Hant': '我會把 Docker 啟動指令加到版本說明裡。',
      }],
    ],
  },
  {
    app: 'muse',
    messages: [
      ['you', {
        en: 'Check my SwB inbox.',
        'zh-Hans': '看一下我的 SwB 邮箱。',
        'zh-Hant': '幫我查看 SwB 信箱。',
      }],
      ['muse', {
        en: 'Grok Bot found a Slack example for your talk.',
        'zh-Hans': 'Grok Bot 为你的演讲找到了一个 Slack 示例。',
        'zh-Hant': 'Grok Bot 幫你找到一個可用在演講中的 Slack 範例。',
      }],
      ['muse', {
        en: 'I asked Grok Bot for one more example.',
        'zh-Hans': '我请 Grok Bot 再找一个示例。',
        'zh-Hant': '我請 Grok Bot 再找一個範例。',
      }],
    ],
  },
  {
    app: 'grok-bot',
    messages: [
      ['grok-bot', {
        en: 'Claude, draft the release notes.',
        'zh-Hans': 'Claude，起草发布说明。',
        'zh-Hant': 'Claude，先寫一份版本說明。',
      }],
      ['grok-bot', {
        en: 'Capy Captain, run the tests.',
        'zh-Hans': 'Capy Captain，运行测试。',
        'zh-Hant': 'Capy Captain，請執行測試。',
      }],
      ['claude', {
        en: 'The draft is ready.',
        'zh-Hans': '草稿写好了。',
        'zh-Hant': '初稿寫好了。',
      }],
      ['capy', {
        en: 'All tests passed.',
        'zh-Hans': '测试全部通过。',
        'zh-Hant': '所有測試都通過了。',
      }],
      ['grok-bot', {
        en: 'I will send the release update.',
        'zh-Hans': '我来发一下发布进展。',
        'zh-Hant': '我會發訊息更新發布進度。',
      }],
    ],
  },
  {
    app: 'slack',
    messages: [
      ['you', {
        en: 'Share the release update here.',
        'zh-Hans': '请在这里汇报发布进展。',
        'zh-Hant': '請在這裡回報發布進度。',
      }],
      ['claude', {
        en: 'The release notes are ready.',
        'zh-Hans': '发布说明写好了。',
        'zh-Hant': '版本說明已經寫好。',
      }],
      ['grok-bot', {
        en: 'I added the Docker command to the announcement.',
        'zh-Hans': '公告里已补上 Docker 启动命令。',
        'zh-Hant': '我把 Docker 啟動指令加進公告了。',
      }],
      ['capy', {
        en: 'I opened pull request 42.',
        'zh-Hans': '我发起了 PR 42。',
        'zh-Hant': '我開了 PR 42。',
      }],
    ],
  },
  {
    app: 'github',
    messages: [
      ['you', {
        en: 'Issue 42 asks for a Docker example.',
        'zh-Hans': '议题 42 请求提供 Docker 示例。',
        'zh-Hant': '議題 42 想要一個 Docker 範例。',
      }],
      ['grok-bot', {
        en: 'I added the documentation label.',
        'zh-Hans': '我加上了文档标签。',
        'zh-Hant': '我加上了文件標籤。',
      }],
      ['grok-bot', {
        en: 'I linked the Docker guide in a comment.',
        'zh-Hans': '我在评论中附上了 Docker 指南。',
        'zh-Hant': '我在留言中附上了 Docker 指南。',
      }],
    ],
  },
  {
    app: 'claude-code',
    messages: [
      ['capy', {
        en: 'Run the build on the paired Mac.',
        'zh-Hans': '请在已配对的 Mac 上运行构建。',
        'zh-Hant': '請在已配對的 Mac 上執行建置。',
      }],
      ['claude-code', {
        en: 'The local build is running.',
        'zh-Hans': '本机构建正在运行。',
        'zh-Hant': '本機正在建置。',
      }],
      ['claude-code', {
        en: 'The build passed. I sent the result to Capy Captain.',
        'zh-Hans': '构建通过。我已将结果发给 Capy Captain。',
        'zh-Hant': '建置已通過。我已將結果傳給 Capy Captain。',
      }],
    ],
  },
];
