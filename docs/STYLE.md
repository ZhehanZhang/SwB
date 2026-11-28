# SwB writing style

This guide covers every public text: the docs, the website, the README, the UI, release notes and promo material.

## Words

Write in ASD-STE100 Simplified Technical English. Use one word for one meaning, and use the same word each time. Define each technical term once, in plain words, where it first appears. Use common words in place of jargon.

## Sentences

Write short positive sentences. A procedure sentence has 20 words at most. A description sentence has 25 words at most. Give one instruction in each sentence. Write steps in the imperative and in the active voice.

## Banned forms

Use none of these in prose. Negations such as no, not, never, without and doesn't. Colons. Semicolons. Em dashes. The middle dot divider. Bulleted lists and numbered lists. Eyebrows, which are small labels above a heading. Secondary lines under a heading. Text that repeats what the layout already shows.

Code blocks, inline code, commands, config keys and URLs keep their own syntax.

## Structure

Carry structure with headings, short paragraphs and tables. Use tabs for alternatives such as install methods. Use a table for a set of parallel facts.

## Chinese

The Simplified Chinese and Traditional Chinese docs follow the same rules. Write them natively, with short plain sentences and one idea per sentence. Keep the terms consistent across pages. Simplified Chinese uses mainland terms. Traditional Chinese uses Taiwan terms. Use full-width punctuation, and use none of the full-width colon, semicolon or dash in prose.

Write zh-Hans to GB/T 15834 and Mainland usage, and zh-Hant to Taiwan punctuation and usage. Keep the technical directness of the English. Put one half-width space between Chinese and Latin letters, digits or inline code. Prefer positive phrasing. When a negation is the only correct and natural Chinese wording, keep it and add the exact phrase to allowedNegation in site/scripts/check-style.mjs.

| en | zh-Hans | zh-Hant |
| - | - | - |
| agent | 智能体 | 代理 |
| coding agent | 编程智能体 | 程式開發代理 |
| owner | 所有者 | 擁有者 |
| owner key | 所有者密钥 | 擁有者金鑰 |
| instance | 实例 | 執行個體 |
| deploy, deployment | 部署 | 部署 |
| self-hosting | 自托管 | 自行託管 |
| block | 模块 | 模組 |
| project | 项目 | 專案 |
| slug | 短名称（slug） | 短名稱（slug） |
| memory | 记忆 | 記憶 |
| scope (memory) | 范围 | 範圍 |
| grant | 授权 | 授權 |
| credential | 凭据 | 憑證 |
| key | 密钥 | 金鑰 |
| paired key | 配对密钥 | 配對金鑰 |
| token | 令牌 | 權杖 |
| secret (webhook secret, env secret) | 密钥 or Secret as the UI names it | 密鑰 or Secret |
| claim link | 认领链接 | 認領連結 |
| sign in, sign-in | 登录 | 登入 |
| sign-in link | 登录链接 | 登入連結 |
| owner login | 所有者登录 | 擁有者登入 |
| passkey | 通行密钥 | 密碼金鑰 |
| target | 目标 | 目標 |
| address (target address such as agent:<name>) | 地址 | 位址 |
| web address, URL of your SwB | 地址 | 網址 |
| route | 路由 | 路由 |
| mailbox | 邮箱 | 信箱 |
| inbox | 收件箱 | 收件匣 |
| inbox cursor, inbox position | 读取位置 | 讀取位置 |
| conversation | 对话 | 對話 |
| handle | 对话标识（handle） | 對話識別碼（handle） |
| session | 会话 | 工作階段 |
| local session | 本地会话 | 本機工作階段 |
| companion | Companion | Companion |
| harness | harness | harness |
| channel mode | 频道模式 | 頻道模式 |
| wake | 唤醒 | 喚醒 |
| message | 消息 | 訊息 |
| reply | 回复 | 回覆 |
| send | 发送 | 傳送 |
| tool (MCP tool) | 工具 | 工具 |
| connector | 连接器 | 連接器 |
| plugin | 插件 | 外掛程式 |
| audit log | 审计日志 | 稽核記錄 |
| trust frame | 信任框架 | 信任框架 |
| tunnel | 隧道 | 通道 |
| repository | 仓库 | 存放庫 |
| issue (GitHub) | 议题 | 議題 |
| pull request | Pull Request | Pull Request |
| thread (Slack) | 消息列 | 討論串 |
| channel (Slack, Discord) | 频道 | 頻道 |
| bot | 机器人 | 機器人 |
| webhook | Webhook | Webhook |
| setup prompt | 设置提示词 | 設定提示詞 |
| triage agent | 分流智能体 | 分流代理 |
| private key | 私钥 | 私密金鑰 |
| setup code | 设置码 | 設定碼 |
| pairing code | 配对码 | 配對碼 |
| memory entry | 条目 | 項目 |
| topic (memory) | 主题 | 主題 |
| provenance | 来源记录 | 來源紀錄 |
| memory sync | 记忆同步 | 記憶同步 |
| deploy key | 部署密钥 | 部署金鑰 |

## Check

Run `node site/scripts/check-style.mjs` before you open a pull request. The script flags the banned forms in prose.
