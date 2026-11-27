import { releaseBranch, repository } from './urls';

export const DEPLOY_BUTTONS_LIVE = false;
const RENDER_DEPLOY_LIVE = true;
const RAILWAY_DEPLOY_LIVE = true;
const renderBlueprint = `https://render.com/deploy?repo=${repository}/tree/${releaseBranch}`;

const paidPlan = {
  en: 'Paid plan',
  'zh-Hans': '付费套餐',
  'zh-Hant': '付費方案',
};

export const deployProviders = {
  fly: {
    mark: 'fly',
    name: 'Fly.io',
    href: '/docs/self-hosting#fly',
    enabled: true,
    label: {
      en: 'Free with monthly allowance',
      'zh-Hans': '月度额度内免费',
      'zh-Hant': '月度額度內免費',
    },
  },
  render: {
    mark: 'render',
    name: 'Render',
    href: DEPLOY_BUTTONS_LIVE
      ? `https://render.com/deploy?repo=${encodeURIComponent(`${repository}/tree/${releaseBranch}`)}`
      : `${repository}/blob/${releaseBranch}/deploy/README.md#render`,
    oneClickHref: RENDER_DEPLOY_LIVE ? renderBlueprint : undefined,
    enabled: true,
    label: paidPlan,
  },
  railway: {
    mark: 'railway',
    name: 'Railway',
    href: '/docs/self-hosting#railway',
    oneClickHref: RAILWAY_DEPLOY_LIVE ? 'https://railway.com/deploy/swb' : undefined,
    enabled: process.env.NODE_ENV === 'development' && process.env.SWB_TEST_DEPLOY_RAILWAY === '1',
    label: {
      en: 'Hobby plan',
      'zh-Hans': 'Hobby 套餐',
      'zh-Hant': 'Hobby 方案',
    },
  },
} as const;
