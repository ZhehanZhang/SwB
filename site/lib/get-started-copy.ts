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
    },
  },
  'zh-Hans': {
    runTitle: 'SwB 的运行位置',
    runHelp: '选择 SwB 的运行位置，下方会显示对应的安装步骤。',
    runLocal: '在本机运行',
    runServer: '在自己的服务器上运行',
    runCloud: '在云端运行',
    agentsTitle: '选择智能体',
    address: '你的 SwB 地址',
    addressHelp: '下方的提示词会使用这个地址。',
    addressError: '请输入有效的 HTTP 或 HTTPS 地址。',
    pipx: '用 pipx 安装',
    copyCommand: '复制命令',
    copied: '已复制',
    copy: '复制提示词',
    open: '在 {app} 中打开',
    guide: '完整指南',
    installGuide: '安装指南',
    selfHostingGuide: '自托管指南',
    reachAnywhere: '从任何地方访问',
    companionGuide: 'Companion 指南',
    companionHelp: '要接入本地的 Claude Code、Codex、OpenCode 或 dsh 会话，智能体会引导你在自己的设备上安装并配对 Companion。',
    museCompanionHelp: '要用 gadget 唤醒 Muse，智能体会引导你在自己的设备上安装并配对 Companion。',
    muse: {
      wake: '用 gadget 唤醒 Muse',
      beforeCommand: '准备一台带蓝牙的 Mac 或 Linux 电脑，放在手机旁边。向拥有 SwB 所有者权限的智能体索取配对码。把示例地址换成你的 SwB 地址。',
      afterCommand: '在 10 分钟内输入这个一次性配对码。电脑请求蓝牙权限时，选择允许。让智能体开始 Muse 配对。在 Muse 应用中选择 Add Device。',
      tools: '让 Muse 使用 SwB 工具',
    },
    procedure: {
      'claude-project': '把设置提示词粘贴到项目的协调者聊天中。为 routine 添加 API 触发器。把它的 Fire URL 交给智能体。用 SwB 认领链接保存 routine 令牌。',
      claude: '在 Claude 中把 SwB 添加为自定义连接器。地址填写以 /mcp 结尾的 SwB 公网地址。登录 SwB 并批准访问。',
      chatgpt: '在浏览器中打开 chatgpt.com/plugins。把 SwB 添加为自定义 MCP 服务器。创建插件时选择 OAuth。登录 SwB。',
      'chatgpt-dots': '把 dots 模块的投递目标设为 SwB 邮箱。在浏览器中把 SwB 添加到 ChatGPT。把设置提示词粘贴给你的 dot。按 dot 给出的说明完成设置。',
      muse: '在聊天中让 Muse 把 SwB 添加为使用 OAuth 的自定义连接器。打开 Muse 发来的链接。以所有者身份登录 SwB。',
      grok: '在浏览器中打开 grok.com/connectors。把 SwB 添加为自定义连接器。登录 SwB。把设置提示词粘贴到新的 Grok 聊天中。',
      'grok-bot': '在 Grok Bot 中以 Remote HTTPS 方式把 SwB 添加为自定义连接器。把设置提示词粘贴到 Bot 聊天中。用对应的 SwB 认领链接保存 routine 的 Webhook URL。再用另一个认领链接保存 Webhook 密钥。',
      capy: '用 SwB 认领链接保存 Capy API 密钥。智能体提示时，重启 SwB。告诉智能体由哪个 Capy Captain 接收任务。用对应的 SwB 认领链接保存服务用户的密钥。',
      'claude-code': '用智能体给出的命令添加 SwB。在 Claude Code 中打开 /mcp。出现提示时，在浏览器中登录 SwB。',
      codex: '把 SwB 的配置添加到 ~/.codex/config.toml。运行 codex mcp login swb 完成登录。',
      gemini: '把 SwB 的配置添加到 ~/.gemini/settings.json。在 Gemini CLI 中运行 /mcp auth swb 完成登录。',
      slack: '按指南中的设置创建 Slack 应用。把应用安装到你的工作区。用 SwB 认领链接保存 Slack 令牌。如果使用机器人，把机器人邀请到要连接的频道。',
      discord: '在 Discord Developer Portal 中创建应用。获取它的 Bot 令牌。用 SwB 认领链接保存令牌。用智能体给出的邀请链接把机器人添加到你的服务器。',
      telegram: '用 BotFather 创建机器人。用 SwB 认领链接保存它的令牌。给机器人发一条私聊消息。要连接群组，使用智能体给出的群组链接。',
      github: '用智能体给出的 Webhook 地址创建 GitHub App。按指南设置权限。用 SwB 认领链接保存 Webhook 密钥和 App 凭据。把 App 安装到要连接的仓库。',
    },
  },
  'zh-Hant': {
    runTitle: 'SwB 的執行位置',
    runHelp: '選擇 SwB 的執行位置，下方會顯示對應的安裝步驟。',
    runLocal: '在本機執行',
    runServer: '在自己的伺服器上執行',
    runCloud: '在雲端執行',
    agentsTitle: '選擇代理',
    address: '你的 SwB 網址',
    addressHelp: '下方的提示詞會使用這個網址。',
    addressError: '請輸入有效的 HTTP 或 HTTPS 網址。',
    pipx: '用 pipx 安裝',
    copyCommand: '複製指令',
    copied: '已複製',
    copy: '複製提示詞',
    open: '在 {app} 中開啟',
    guide: '完整指南',
    installGuide: '安裝指南',
    selfHostingGuide: '自行託管指南',
    reachAnywhere: '從任何地方存取',
    companionGuide: 'Companion 指南',
    companionHelp: '要連接本機的 Claude Code、Codex、OpenCode 或 dsh 工作階段，代理會引導你在自己的裝置上安裝並配對 Companion。',
    museCompanionHelp: '要用 gadget 喚醒 Muse，代理會引導你在自己的裝置上安裝並配對 Companion。',
    muse: {
      wake: '用 gadget 喚醒 Muse',
      beforeCommand: '準備一台有藍牙的 Mac 或 Linux 電腦，放在手機旁邊。向具備 SwB 擁有者存取權的代理索取配對碼。把範例網址換成你的 SwB 網址。',
      afterCommand: '在 10 分鐘內輸入這組一次性配對碼。電腦要求藍牙權限時，選擇允許。請代理開始 Muse 配對。在 Muse 應用程式中選取 Add Device。',
      tools: '讓 Muse 使用 SwB 工具',
    },
    procedure: {
      'claude-project': '把設定提示詞貼到專案的協調者聊天中。為 routine 新增 API 觸發條件。把它的 Fire URL 交給代理。用 SwB 認領連結儲存 routine 權杖。',
      claude: '在 Claude 中將 SwB 新增為自訂連接器。網址填入以 /mcp 結尾的 SwB 公開網址。登入 SwB 並核准存取。',
      chatgpt: '在瀏覽器中開啟 chatgpt.com/plugins。將 SwB 新增為自訂 MCP 伺服器。建立外掛程式時選擇 OAuth。登入 SwB。',
      'chatgpt-dots': '把 dots 模組的傳遞方式設為 SwB 信箱。在瀏覽器中把 SwB 加入 ChatGPT。把設定提示詞貼給你的 dot。依照 dot 的說明完成設定。',
      muse: '在聊天中請 Muse 把 SwB 新增為使用 OAuth 的自訂連接器。開啟 Muse 傳來的連結。以擁有者身分登入 SwB。',
      grok: '在瀏覽器中開啟 grok.com/connectors。將 SwB 新增為自訂連接器。登入 SwB。把設定提示詞貼到新的 Grok 聊天中。',
      'grok-bot': '在 Grok Bot 中以 Remote HTTPS 方式將 SwB 新增為自訂連接器。把設定提示詞貼到 Bot 聊天中。用對應的 SwB 認領連結儲存 routine 的 Webhook URL。再用另一個認領連結儲存 Webhook 金鑰。',
      capy: '用 SwB 認領連結儲存 Capy API 金鑰。代理提示時，重新啟動 SwB。告訴代理要由哪個 Capy Captain 接收工作。用對應的 SwB 認領連結儲存服務使用者的金鑰。',
      'claude-code': '用代理提供的指令新增 SwB。在 Claude Code 中開啟 /mcp。出現提示時，在瀏覽器中登入 SwB。',
      codex: '把 SwB 的設定加入 ~/.codex/config.toml。執行 codex mcp login swb 完成登入。',
      gemini: '把 SwB 的設定加入 ~/.gemini/settings.json。在 Gemini CLI 中執行 /mcp auth swb 完成登入。',
      slack: '依照指南中的設定建立 Slack 應用程式。把應用程式安裝到你的工作空間。用 SwB 認領連結儲存 Slack 權杖。如果使用機器人，把它邀請到要連接的頻道。',
      discord: '在 Discord Developer Portal 建立應用程式。取得它的 Bot 權杖。用 SwB 認領連結儲存權杖。用代理提供的邀請連結把機器人加入你的伺服器。',
      telegram: '用 BotFather 建立機器人。用 SwB 認領連結儲存它的權杖。傳一則私人訊息給機器人。要連接群組，使用代理提供的群組連結。',
      github: '用代理提供的 Webhook 網址建立 GitHub App。依照指南設定權限。用 SwB 認領連結儲存 Webhook 密鑰和應用程式憑證。把應用程式安裝到要連接的存放庫。',
    },
  },
};
