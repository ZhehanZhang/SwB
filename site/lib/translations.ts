import { aiChatTranslations } from '@fumadocs/ai-chat/i18n';
import { zhCN } from '@fumadocs/language/zh-cn';
import { zhTW } from '@fumadocs/language/zh-tw';
import { uiTranslations } from 'fumadocs-ui/i18n';
import { i18n } from './i18n';

export const translations = i18n.translations()
  .extend(uiTranslations())
  .extend(aiChatTranslations())
  .preset('zh-Hans', zhCN())
  .preset('zh-Hant', zhTW())
  .add({
    en: {
      displayName: 'EN',
      'Open(page actions)': 'Open in',
      'Ask a question(AI chat)': 'Question',
      'Ask a follow-up(AI chat)': 'Follow-up',
      'What do you want to know?(AI chat)': '',
    },
    'zh-Hans': {
      displayName: '简体',
      'Search(search trigger)': '搜索文档',
      'Search(search dialog)': '搜索文档',
      'On this page(table of contents)': '本页内容',
      'Open(page actions)': '打开方式',
      'Ask a question(AI chat)': '提问',
      'Ask a follow-up(AI chat)': '追问',
      'What do you want to know?(AI chat)': '',
    },
    'zh-Hant': {
      displayName: '繁體',
      'Search(search trigger)': '搜尋文件',
      'Search(search dialog)': '搜尋文件',
      'On this page(table of contents)': '本頁內容',
      'Open(page actions)': '開啟方式',
      'Ask a question(AI chat)': '提問',
      'Ask a follow-up(AI chat)': '追問',
      'What do you want to know?(AI chat)': '',
    },
  });
