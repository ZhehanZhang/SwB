import type { Locale } from './i18n';

type GetStartedCopy = {
  runTitle: string;
  runHelp: string;
  runLocal: string;
  runServer: string;
  runCloud: string;
  agentsTitle: string;
  address: string;
  addressHelp: string;
  addressError: string;
  pipx: string;
  copyCommand: string;
  copied: string;
  copy: string;
  open: string;
  guide: string;
  installGuide: string;
  selfHostingGuide: string;
  reachAnywhere: string;
  companionGuide: string;
  companionHelp: string;
  museCompanionHelp: string;
  muse: { wake: string; beforeCommand: string; afterCommand: string; tools: string };
  procedure: Record<string, string>;
};

export const getStartedCopy: Record<Locale, GetStartedCopy> = {
  en: {
    runTitle: 'Where SwB runs',
    runHelp: 'Choose where SwB runs to see the install instructions for that location.',
    runLocal: 'On your computer',
    runServer: 'On your server',
    runCloud: 'In the cloud',
    agentsTitle: 'Pick your agents',
    address: 'Your SwB address',
    addressHelp: 'The prompts below use your SwB address.',
    addressError: 'Enter a valid HTTP or HTTPS URL.',
    pipx: 'Use pipx',
    copyCommand: 'Copy command',
    copied: 'Copied',
    copy: 'Copy prompt',
    open: 'Open in {app}',
    guide: 'Full guide',
    installGuide: 'Install guide',
    selfHostingGuide: 'Self-hosting guide',
    reachAnywhere: 'Reach it from anywhere',
    companionGuide: 'Companion guide',
    companionHelp: 'For local Claude Code, Codex, OpenCode or dsh sessions, your agent guides companion installation and pairing on your device.',
    museCompanionHelp: 'For gadget wakes, your agent guides you to install and pair a companion on your device.',
    muse: {
      wake: 'Wake Muse with a gadget',
      beforeCommand: 'Use a Mac or Linux computer with Bluetooth near your phone. Ask an agent with SwB owner access for a pairing code. Replace the example address with your SwB address.',
      afterCommand: 'Enter the single-use code within ten minutes. Allow Bluetooth when your computer asks. Ask your agent to start Muse pairing. Select Add Device in the Muse app.',
      tools: 'Connect Muse to SwB tools',
    },
    procedure: {
      'claude-project': 'Paste the setup prompt into your project coordinator chat. Add an API trigger to the routine. Give its Fire URL to your agent. Save the routine token through the SwB claim link.',
      claude: 'Add SwB as a custom connector in Claude. Use your public SwB address ending in /mcp. Sign in to SwB. Approve access.',
      chatgpt: 'Open chatgpt.com/plugins in a browser. Add SwB as a custom MCP server. Choose OAuth when you create the plugin. Sign in to SwB.',
      'chatgpt-dots': 'Set the dots block to deliver to a SwB mailbox. Add SwB to ChatGPT in your browser. Paste the setup prompt into your dot. Follow the setup instructions from your dot.',
      muse: 'Ask Muse in a chat to add SwB as a custom connector with OAuth. Open the link from Muse. Sign in to SwB as the owner.',
      grok: 'Open grok.com/connectors in your browser. Add SwB as a custom connector. Sign in to SwB. Paste the setup prompt into a new Grok chat.',
      'grok-bot': 'Add SwB as a custom connector with Remote HTTPS in Grok Bot. Paste the setup prompt into your Bot chat. Save the routine webhook URL through its SwB claim link. Save the webhook key through its own claim link.',
      capy: 'Save your Capy API key through the SwB claim link. Restart SwB when your agent asks. Tell your agent which Capy Captain gets the work. Save the service user key through its SwB claim link.',
      'claude-code': 'Add SwB with the command from your agent. Open /mcp in Claude Code. Sign in to SwB in your browser when prompted.',
      codex: 'Add the SwB entry to ~/.codex/config.toml. Run codex mcp login swb to sign in.',
      gemini: 'Add the SwB entry to ~/.gemini/settings.json. Run /mcp auth swb in Gemini CLI to sign in.',
      slack: 'Create a Slack app with the settings from the guide. Install it in your workspace. Save your Slack tokens through the SwB claim link. For a bot setup, invite the bot to the channels you want to connect.',
      discord: 'Create an app in the Discord Developer Portal. Get its bot token. Save the token through the SwB claim link. Add the bot to your server with the invite link from your agent.',
      telegram: 'Create a bot with BotFather. Save its token through the SwB claim link. Send a private message to your bot. To connect a group, use the group link from your agent.',
      github: 'Create a GitHub App with the webhook address from your agent. Set the permissions in the guide. Save the webhook secret and app credentials through the SwB claim links. Install the app on the repositories you want to connect.',
      webhook: 'Choose the sending service and the event target. Save the signing secret through the SwB claim link. Test the connection.',
    },
  },
  'zh-Hans': {
    runTitle: 'SwB 在哪里运行',
    runHelp: '选择 SwB 的运行位置，查看对应的安装说明。',
    runLocal: '在本机运行',
    runServer: '在自己的服务器上运行',
    runCloud: '在云端运行',
    agentsTitle: '选择智能体',
    address: '你的 SwB 地址',
    addressHelp: '下方的提示词会使用这个 SwB 地址。',
    addressError: '请输入有效的 HTTP 或 HTTPS 网址。',
    pipx: '用 pipx 安装',
    copyCommand: '复制命令',
    copied: '已复制',
    copy: '复制提示词',
    open: '在 {app} 中打开',
    guide: '完整指南',
    installGuide: '安装指南',
    selfHostingGuide: '自托管指南',
    reachAnywhere: '随处访问',
    companionGuide: '伴侣设备指南',
    companionHelp: '如果使用 Claude Code、Codex、OpenCode 或 dsh 的本机会话，智能体会指导你在自己的设备上安装并配对 SwB 伴侣设备。',
    museCompanionHelp: '设置实时唤醒时，智能体会指导你在自己的设备上安装并配对 SwB 伴侣设备。',
    muse: {
      wake: '用配对设备唤醒 Muse',
      beforeCommand: '在手机附近使用配有蓝牙的 Mac 或 Linux 电脑。请拥有 SwB 所有者权限的智能体提供配对码。将命令中的示例地址换成你的 SwB 地址。',
      afterCommand: '在十分钟内输入一次性配对码。电脑提示时允许蓝牙访问。请智能体开始 Muse 配对。在 Muse 应用中选择 Add Device。',
      tools: '让 Muse 使用 SwB 工具',
    },
    procedure: {
      'claude-project': '把设置提示词粘贴到项目协调者的会话中。为自动任务添加 API 触发器。把触发地址发给智能体。通过 SwB 领取链接保存自动任务的令牌。',
      claude: '在 Claude 中把 SwB 添加为自定义连接器。填入以 /mcp 结尾的 SwB 公网地址。登录 SwB。批准访问。',
      chatgpt: '在浏览器中打开 chatgpt.com/plugins。把 SwB 添加为自定义 MCP 服务器。创建插件时选择 OAuth。登录 SwB。',
      'chatgpt-dots': '将 dots 模块设为通过 SwB 邮箱接收消息。在网页版 ChatGPT 中添加 SwB。把设置提示词粘贴到 dot 的会话中。按 dot 给出的说明完成设置。',
      muse: '在 Muse 会话中请 Muse 把 SwB 添加为采用 OAuth 的自定义连接器。打开 Muse 发来的登录链接。以所有者身份登录 SwB。',
      grok: '在浏览器中打开 grok.com/connectors。把 SwB 添加为自定义连接器。登录 SwB。把设置提示词粘贴到新的 Grok 会话中。',
      'grok-bot': '在 Grok Bot 中用 Remote HTTPS 添加 SwB 自定义连接器。把设置提示词粘贴到 Bot 会话中。通过 SwB 领取链接保存自动任务的 Webhook 地址。通过另一个领取链接保存 Webhook 密钥。',
      capy: '通过 SwB 领取链接保存你的 Capy API 密钥。按智能体的提示重启 SwB。告诉智能体由哪个 Capy Captain 接收任务。通过对应的 SwB 领取链接保存服务用户的密钥。',
      'claude-code': '运行智能体提供的命令来添加 SwB。在 Claude Code 中打开 /mcp。按提示在浏览器中登录 SwB。',
      codex: '将 SwB 的配置项添加到 ~/.codex/config.toml。运行 codex mcp login swb 登录。',
      gemini: '将 SwB 的配置项添加到 ~/.gemini/settings.json。在 Gemini CLI 中运行 /mcp auth swb 登录。',
      slack: '按指南中的配置创建 Slack 应用。把应用安装到工作区。通过 SwB 领取链接保存 Slack 令牌。如果使用机器人，邀请它加入要连接的频道。',
      discord: '在 Discord 开发者门户创建应用。获取该应用的机器人令牌。通过 SwB 领取链接保存令牌。用智能体给出的邀请链接将机器人添加到服务器。',
      telegram: '通过 BotFather 创建机器人。通过 SwB 领取链接保存机器人的令牌。给机器人发一条私信。要连接群组，请使用智能体给出的群组链接。',
      github: '用智能体给出的 Webhook 地址创建 GitHub App。按指南设置权限。通过 SwB 领取链接保存 Webhook 密钥和应用凭据。把应用安装到要连接的仓库。',
      webhook: '选择发送服务和事件目标。通过 SwB 领取链接保存签名密钥。测试连接。',
    },
  },
  'zh-Hant': {
    runTitle: 'SwB 在哪裡執行',
    runHelp: '選擇要執行 SwB 的位置，即可查看對應的安裝說明。',
    runLocal: '在自己的電腦上執行',
    runServer: '在自己的伺服器上執行',
    runCloud: '在雲端執行',
    agentsTitle: '選擇代理',
    address: '你的 SwB 位址',
    addressHelp: '下方的提示詞會使用這個 SwB 位址。',
    addressError: '請輸入有效的 HTTP 或 HTTPS 網址。',
    pipx: '用 pipx 安裝',
    copyCommand: '複製指令',
    copied: '已複製',
    copy: '複製提示詞',
    open: '在 {app} 中開啟',
    guide: '完整指南',
    installGuide: '安裝指南',
    selfHostingGuide: '自行代管指南',
    reachAnywhere: '隨處存取',
    companionGuide: '夥伴裝置指南',
    companionHelp: '如果使用 Claude Code、Codex、OpenCode 或 dsh 的本機對話，代理會引導你在自己的裝置上安裝並配對 SwB 夥伴裝置。',
    museCompanionHelp: '設定即時喚醒時，代理會引導你在自己的裝置上安裝並配對 SwB 夥伴裝置。',
    muse: {
      wake: '用配對裝置喚醒 Muse',
      beforeCommand: '在手機附近使用支援藍牙的 Mac 或 Linux 電腦。請具有 SwB 擁有者權限的代理提供配對碼。將指令中的範例位址換成你的 SwB 位址。',
      afterCommand: '在十分鐘內輸入一次性配對碼。電腦提示時允許使用藍牙。請代理開始配對 Muse。在 Muse 應用程式中選擇 Add Device。',
      tools: '讓 Muse 使用 SwB 工具',
    },
    procedure: {
      'claude-project': '把設定提示詞貼到專案協調者的對話。替 routine 加上 API 觸發條件。將觸發網址交給代理。透過 SwB 領取連結儲存 routine 權杖。',
      claude: '在 Claude 裡將 SwB 加入為自訂連接器。填入以 /mcp 結尾的 SwB 公開網址。登入 SwB。核准存取。',
      chatgpt: '用瀏覽器開啟 chatgpt.com/plugins。將 SwB 加入為自訂 MCP 伺服器。建立外掛程式時選擇 OAuth。登入 SwB。',
      'chatgpt-dots': '將 dots 模組設為透過 SwB 信箱接收訊息。在網頁版 ChatGPT 加入 SwB。把設定提示詞貼到 dot 的對話。依照 dot 的指示完成設定。',
      muse: '在 Muse 的對話裡，請 Muse 用 OAuth 把 SwB 加入為自訂連接器。開啟 Muse 傳來的登入連結。以擁有者身分登入 SwB。',
      grok: '用瀏覽器開啟 grok.com/connectors。將 SwB 加入為自訂連接器。登入 SwB。把設定提示詞貼到新的 Grok 對話。',
      'grok-bot': '在 Grok Bot 用 Remote HTTPS 新增 SwB 自訂連接器。把設定提示詞貼到 Bot 對話。透過 SwB 領取連結儲存 routine 的 Webhook 網址。透過另一個領取連結儲存 Webhook 金鑰。',
      capy: '透過 SwB 領取連結儲存你的 Capy API 金鑰。依照代理的指示重新啟動 SwB。告訴代理要把工作交給哪位 Capy Captain。透過對應的 SwB 領取連結儲存服務使用者的金鑰。',
      'claude-code': '執行代理提供的指令來加入 SwB。在 Claude Code 開啟 /mcp。依照提示在瀏覽器登入 SwB。',
      codex: '在 ~/.codex/config.toml 加入 SwB 的設定。執行 codex mcp login swb 來登入。',
      gemini: '在 ~/.gemini/settings.json 加入 SwB 的設定。在 Gemini CLI 執行 /mcp auth swb 來登入。',
      slack: '依照指南的設定建立 Slack 應用程式。把應用程式安裝到工作區。透過 SwB 領取連結儲存 Slack 權杖。若使用機器人，請邀請它加入要連線的頻道。',
      discord: '在 Discord 開發者入口網站建立應用程式。取得這個應用程式的機器人權杖。透過 SwB 領取連結儲存權杖。用代理提供的邀請連結將機器人加入伺服器。',
      telegram: '用 BotFather 建立機器人。透過 SwB 領取連結儲存機器人的權杖。傳一則私人訊息給機器人。若要連線到群組，請使用代理給你的群組連結。',
      github: '用代理提供的 Webhook 網址建立 GitHub App。依照指南設定權限。透過 SwB 領取連結儲存 Webhook 金鑰與應用程式憑證。將應用程式安裝到要連線的程式碼庫。',
      webhook: '選擇傳送服務和事件目標。透過 SwB 領取連結儲存簽署金鑰。測試連線。',
    },
  },
};
