import type { Locale } from './i18n';

export type Party = 'claude' | 'grok-bot' | 'capy' | 'dots' | 'muse';
type Task = { ask: string; reply: string };
type SlackLine = ['you' | Party, string, string?];

type Copy = {
  title: string;
  modes: { agents: string; slack: string };
  from: string;
  to: string;
  you: string;
  names: Record<Party, string>;
  ask: (name: string, task: string) => string;
  sent: (name: string) => string;
  tasks: Record<Party, Task>;
  slack: SlackLine[];
};

const names: Record<Party, string> = { claude: 'Claude', 'grok-bot': 'Grok Bot', capy: 'Captain', dots: 'Dot', muse: 'Muse' };

export const exchangeCopy: Record<Locale, Copy> = {
  en: {
    title: 'Ask one agent to work with another',
    modes: { agents: 'Agent to agent', slack: 'Slack bot' },
    from: 'Who starts',
    to: 'Who they ask',
    you: 'You',
    names,
    ask: (name, task) => `Ask ${name} to ${task}.`,
    sent: (name) => `Sent to ${name} through SwB.`,
    tasks: {
      claude: { ask: 'draft the release notes from the merged PRs', reply: 'The draft is in the Launch project.' },
      'grok-bot': { ask: "check today's posts about the launch", reply: 'I found 14 posts. Most ask about Docker setup.' },
      capy: { ask: 'open a PR with the release notes', reply: 'PR 42 is open with the notes.' },
      dots: { ask: "sum up this week's feedback", reply: 'Setup speed and Muse pairing come up most.' },
      muse: { ask: 'remind me about the launch at 5 pm', reply: 'I set a reminder for 5 pm.' },
    },
    slack: [
      ['you', '@SwB claude draft the release notes'],
      ['claude', 'The draft is in the Launch project.'],
      ['you', "@SwB grok-bot check today's launch posts"],
      ['grok-bot', 'I found 14 posts. Most ask about Docker setup.'],
      ['you', '@SwB atlas open the release PR'],
      ['capy', 'PR 42 is open with the notes.', 'Atlas'],
    ],
  },
  'zh-Hans': {
    title: '让一个智能体请另一个智能体帮忙',
    modes: { agents: '智能体之间', slack: 'Slack 机器人' },
    from: '谁先开始',
    to: '交给谁',
    you: '你',
    names,
    ask: (name, task) => `请 ${name}${task}。`,
    sent: (name) => `已通过 SwB 发给 ${name}。`,
    tasks: {
      claude: { ask: '根据已合并的 PR 起草发布说明', reply: '草稿已存入 Launch 项目。' },
      'grok-bot': { ask: '查看今天关于发布的帖子', reply: '我找到 14 条帖子，大多在问 Docker 设置。' },
      capy: { ask: '开一个包含发布说明的 PR', reply: 'PR 42 已开启，包含发布说明。' },
      dots: { ask: '总结本周的反馈', reply: '提到最多的是设置速度和 Muse 配对。' },
      muse: { ask: '下午 5 点提醒我发布的事', reply: '我已设好下午 5 点的提醒。' },
    },
    slack: [
      ['you', '@SwB claude 起草发布说明'],
      ['claude', '草稿已存入 Launch 项目。'],
      ['you', '@SwB grok-bot 查看今天关于发布的帖子'],
      ['grok-bot', '我找到 14 条帖子，大多在问 Docker 设置。'],
      ['you', '@SwB atlas 开发布 PR'],
      ['capy', 'PR 42 已开启，包含发布说明。', 'Atlas'],
    ],
  },
  'zh-Hant': {
    title: '請一個代理找另一個代理幫忙',
    modes: { agents: '代理之間', slack: 'Slack 機器人' },
    from: '誰先開始',
    to: '交給誰',
    you: '你',
    names,
    ask: (name, task) => `請 ${name}${task}。`,
    sent: (name) => `已透過 SwB 傳給 ${name}。`,
    tasks: {
      claude: { ask: '根據已合併的 PR 起草版本說明', reply: '草稿已存入 Launch 專案。' },
      'grok-bot': { ask: '查看今天關於發布的貼文', reply: '我找到 14 則貼文，多數在問 Docker 設定。' },
      capy: { ask: '開一個包含版本說明的 PR', reply: 'PR 42 已開啟，包含版本說明。' },
      dots: { ask: '總結本週的意見回饋', reply: '最常提到的是設定速度與 Muse 配對。' },
      muse: { ask: '下午 5 點提醒我發布的事', reply: '我已設好下午 5 點的提醒。' },
    },
    slack: [
      ['you', '@SwB claude 起草版本說明'],
      ['claude', '草稿已存入 Launch 專案。'],
      ['you', '@SwB grok-bot 查看今天關於發布的貼文'],
      ['grok-bot', '我找到 14 則貼文，多數在問 Docker 設定。'],
      ['you', '@SwB atlas 開版本 PR'],
      ['capy', 'PR 42 已開啟，包含版本說明。', 'Atlas'],
    ],
  },
};
