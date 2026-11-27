import type { Locale } from './i18n';

export const exchangeCopy = {
  en: { title: 'Agents work together', you: 'You' },
  'zh-Hans': { title: '智能体协同工作', you: '你' },
  'zh-Hant': { title: '代理協同工作', you: '你' },
} satisfies Record<Locale, { title: string; you: string }>;

export const agentNames = {
  claude: 'Claude',
  'grok-bot': 'Grok Bot',
  capy: 'Capy',
  muse: 'Muse',
};

type Message = [speaker: keyof typeof agentNames | 'you', text: Record<Locale, string>];

export const exchangeScenes: { app: keyof typeof agentNames | 'slack'; messages: Message[] }[] = [
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
      ['muse', {
        en: 'Grok Bot, find an example for today’s talk.',
        'zh-Hans': 'Grok Bot，给今天的演讲找个例子。',
        'zh-Hant': 'Grok Bot，幫今天的演講找個例子。',
      }],
      ['grok-bot', {
        en: 'A Slack bot can post as each agent.',
        'zh-Hans': '一个 Slack 机器人可以用不同智能体的身份发消息。',
        'zh-Hant': '同一個 Slack 機器人可以用各個代理的身分發訊息。',
      }],
      ['muse', {
        en: 'I will use that example in the talk.',
        'zh-Hans': '演讲就用这个例子。',
        'zh-Hant': '我會在演講裡用這個例子。',
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
        en: 'Capy, run the tests.',
        'zh-Hans': 'Capy，运行测试。',
        'zh-Hant': 'Capy，請執行測試。',
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
        en: '@SwB, ask the agents for a release update.',
        'zh-Hans': '@SwB，问问各个智能体，发布进展如何？',
        'zh-Hant': '@SwB，請各個代理回報發布進度。',
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
];
