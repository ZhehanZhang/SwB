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
export type ExchangeScene = { app: keyof typeof agentNames; messages: [Message, Message, Message, Message] };

export const exchangeScenes: ExchangeScene[] = [
  {
    app: 'claude',
    messages: [
      ['claude', {
        en: 'Grok Bot, read the release replies.',
        'zh-Hans': 'Grok Bot，看看发布公告下的回复。',
        'zh-Hant': 'Grok Bot，看看版本公告下的留言。',
      }],
      ['grok-bot', {
        en: 'Readers asked for a Docker example.',
        'zh-Hans': '有读者想要一个 Docker 示例。',
        'zh-Hant': '有讀者想要一個 Docker 範例。',
      }],
      ['claude', {
        en: 'I added one to the release notes.',
        'zh-Hans': '我把示例加到了发布说明里。',
        'zh-Hant': '我把範例加進版本說明了。',
      }],
      ['grok-bot', {
        en: 'I sent the notes to Capy Captain.',
        'zh-Hans': '我把说明发给了 Capy Captain。',
        'zh-Hant': '我把說明傳給 Capy Captain 了。',
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
        en: 'Grok Bot sent a Slack example.',
        'zh-Hans': 'Grok Bot 发来了一个 Slack 示例。',
        'zh-Hant': 'Grok Bot 傳來一個 Slack 範例。',
      }],
      ['you', {
        en: 'Ask for one more example.',
        'zh-Hans': '请让 Grok Bot 再找一个。',
        'zh-Hant': '請讓 Grok Bot 再找一個。',
      }],
      ['muse', {
        en: 'I sent the request to Grok Bot.',
        'zh-Hans': '我已经问 Grok Bot 了。',
        'zh-Hant': '我已經問 Grok Bot 了。',
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
      ['claude', {
        en: 'The draft is ready.',
        'zh-Hans': '草稿写好了。',
        'zh-Hant': '初稿寫好了。',
      }],
      ['grok-bot', {
        en: 'Capy Captain, run the tests.',
        'zh-Hans': 'Capy Captain，运行测试。',
        'zh-Hant': 'Capy Captain，請執行測試。',
      }],
      ['capy', {
        en: 'All tests passed.',
        'zh-Hans': '测试全部通过。',
        'zh-Hant': '所有測試都通過了。',
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
        en: 'I added a Docker example.',
        'zh-Hans': '我补上了 Docker 示例。',
        'zh-Hant': '我補上了 Docker 範例。',
      }],
      ['capy', {
        en: 'I posted the test results.',
        'zh-Hans': '我把测试结果发到这里了。',
        'zh-Hant': '我把測試結果貼在這裡了。',
      }],
    ],
  },
  {
    app: 'github',
    messages: [
      ['you', {
        en: 'Please add a Docker example.',
        'zh-Hans': '请加一个 Docker 示例。',
        'zh-Hant': '請加一個 Docker 範例。',
      }],
      ['grok-bot', {
        en: 'I added the documentation label.',
        'zh-Hans': '我加上了文档标签。',
        'zh-Hant': '我加上了文件標籤。',
      }],
      ['grok-bot', {
        en: 'I linked the Docker guide.',
        'zh-Hans': '我附上了 Docker 指南链接。',
        'zh-Hant': '我附上了 Docker 指南連結。',
      }],
      ['you', {
        en: 'The guide answered my question.',
        'zh-Hans': '我在指南里找到了答案。',
        'zh-Hant': '我在指南裡找到了答案。',
      }],
    ],
  },
  {
    app: 'claude-code',
    messages: [
      ['capy', {
        en: 'Build the site on the paired Mac.',
        'zh-Hans': '请在已配对的 Mac 上构建网站。',
        'zh-Hant': '請在已配對的 Mac 上建置網站。',
      }],
      ['claude-code', {
        en: 'The local build is running.',
        'zh-Hans': '本机构建正在运行。',
        'zh-Hant': '本機正在建置。',
      }],
      ['claude-code', {
        en: 'The build passed.',
        'zh-Hans': '网站构建完成。',
        'zh-Hant': '網站建置完成。',
      }],
      ['capy', {
        en: 'I sent the result to Slack.',
        'zh-Hans': '我把结果发到 Slack 了。',
        'zh-Hant': '我把結果傳到 Slack 了。',
      }],
    ],
  },
];
