import type { MarkKind } from '@/components/mark-tile';
import type { Locale } from './i18n';

export type Target = 'claude' | 'chatgpt' | 'grok';

export const openUrl: Record<Target, (prompt: string) => string> = {
  claude: (prompt) => `https://claude.ai/new?q=${encodeURIComponent(prompt)}`,
  chatgpt: (prompt) => `https://chatgpt.com/?q=${encodeURIComponent(prompt)}`,
  grok: (prompt) => `https://grok.com/?q=${encodeURIComponent(prompt)}`,
};

type Step = Record<Locale, string>;

export const setup: Record<MarkKind, { prompt: string; open: Target[]; docs: string; step: Step } | undefined> = {
  claude: { prompt: 'claude-project', open: ['claude'], docs: '/docs/blocks/claude', step: {
    en: 'Make the API token on the routine page in Claude.',
    'zh-Hans': '在 Claude 的 routine 页面生成 API 令牌。',
    'zh-Hant': '在 Claude 的 routine 頁面產生 API 權杖。' } },
  chatgpt: { prompt: 'chatgpt', open: ['chatgpt'], docs: '/docs/blocks/chatgpt', step: {
    en: 'Add SwB as a custom MCP server on chatgpt.com in a web browser.',
    'zh-Hans': '在浏览器中打开 chatgpt.com，把 SwB 添加为自定义 MCP 服务器。',
    'zh-Hant': '在瀏覽器中開啟 chatgpt.com，把 SwB 加入為自訂 MCP 伺服器。' } },
  dots: { prompt: 'dots', open: ['chatgpt'], docs: '/docs/blocks/chatgpt', step: {
    en: 'Add SwB to ChatGPT on the web, then ask your dot to connect.',
    'zh-Hans': '先在网页版 ChatGPT 中添加 SwB，再让你的 dot 完成连接。',
    'zh-Hant': '先在網頁版 ChatGPT 中加入 SwB，再請你的 dot 完成連線。' } },
  capy: { prompt: 'capy-captain', open: ['claude', 'chatgpt'], docs: '/docs/blocks/capy', step: {
    en: 'Make a Capy API key and paste it on the SwB claim page.',
    'zh-Hans': '生成 Capy API 密钥，并粘贴到 SwB 领取页面。',
    'zh-Hant': '產生 Capy API 金鑰，並貼到 SwB 領取頁面。' } },
  grok: { prompt: 'grok', open: ['grok'], docs: '/docs/blocks/grok', step: {
    en: 'Add SwB as a custom connector on grok.com.',
    'zh-Hans': '在 grok.com 把 SwB 添加为自定义连接器。',
    'zh-Hant': '在 grok.com 把 SwB 加入為自訂連接器。' } },
  'grok-bot': { prompt: 'grok-bot', open: ['grok'], docs: '/docs/blocks/grok', step: {
    en: 'Add SwB in Grok Bot under Connect apps and paste the routine token on the claim page.',
    'zh-Hans': '在 Grok Bot 的 Connect apps 中添加 SwB，并把 routine 令牌粘贴到领取页面。',
    'zh-Hant': '在 Grok Bot 的 Connect apps 中加入 SwB，並把 routine 權杖貼到領取頁面。' } },
  codex: { prompt: 'coding-agent', open: [], docs: '/docs/blocks/coding-agents', step: {
    en: 'Run one command in your terminal.', 'zh-Hans': '在终端运行一条命令。', 'zh-Hant': '在終端機執行一個指令。' } },
  'claude-code': { prompt: 'coding-agent', open: [], docs: '/docs/blocks/coding-agents', step: {
    en: 'Run one command in your terminal.', 'zh-Hans': '在终端运行一条命令。', 'zh-Hant': '在終端機執行一個指令。' } },
  gemini: { prompt: 'coding-agent', open: [], docs: '/docs/blocks/coding-agents', step: {
    en: 'Run one command in your terminal.', 'zh-Hans': '在终端运行一条命令。', 'zh-Hant': '在終端機執行一個指令。' } },
  muse: { prompt: 'muse', open: ['claude', 'chatgpt'], docs: '/docs/blocks/muse', step: {
    en: 'Pair a Linux device with the Muse app over Bluetooth.',
    'zh-Hans': '通过蓝牙把一台 Linux 设备与 Muse 应用配对。',
    'zh-Hant': '透過藍牙把一台 Linux 裝置與 Muse 應用程式配對。' } },
  slack: { prompt: 'slack', open: ['claude', 'chatgpt'], docs: '/docs/blocks/slack', step: {
    en: 'Create the Slack app from the manifest and paste its tokens on the claim page.',
    'zh-Hans': '用清单创建 Slack 应用，并把令牌粘贴到领取页面。',
    'zh-Hant': '用資訊清單建立 Slack 應用程式，並把權杖貼到領取頁面。' } },
  discord: { prompt: 'discord', open: ['claude', 'chatgpt'], docs: '/docs/blocks/discord', step: {
    en: 'Create a bot in the Discord Developer Portal and paste its token on the claim page.',
    'zh-Hans': '在 Discord 开发者门户创建机器人，并把令牌粘贴到领取页面。',
    'zh-Hant': '在 Discord 開發者入口網站建立機器人，並把權杖貼到領取頁面。' } },
  telegram: { prompt: 'telegram', open: ['claude', 'chatgpt'], docs: '/docs/blocks/telegram', step: {
    en: 'Create a bot with BotFather and paste its token on the claim page.',
    'zh-Hans': '用 BotFather 创建机器人，并把令牌粘贴到领取页面。',
    'zh-Hant': '用 BotFather 建立機器人，並把權杖貼到領取頁面。' } },
  github: { prompt: 'github', open: ['claude', 'chatgpt'], docs: '/docs/blocks/github', step: {
    en: 'Create a GitHub App and install it on your repositories.',
    'zh-Hans': '创建 GitHub App，并安装到你的仓库。',
    'zh-Hant': '建立 GitHub App，並安裝到你的程式碼庫。' } },
  swb: undefined,
};
