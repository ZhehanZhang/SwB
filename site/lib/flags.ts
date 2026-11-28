import { releaseBranch, repository } from './urls';

export const DEPLOY_BUTTONS_LIVE = false;
const RENDER_DEPLOY_LIVE = true;
const RAILWAY_DEPLOY_LIVE = true;
const renderBlueprint = `https://render.com/deploy?repo=${repository}/tree/${releaseBranch}`;

const paidPlan = {
  en: 'Paid plan',
  'zh-Hans': '付费套餐',
  'zh-Hant': '付費方案',
  es: "Plan de pago",
  fr: "Offre payante",
  de: "Kostenpflichtig",
  ko: "유료 플랜",
  ja: "有料プラン",
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
      es: "Gratis dentro de la cuota mensual",
      fr: "Gratuit avec quota mensuel",
      de: "Kostenlos mit Monatskontingent",
      ko: "월 무료 사용량 제공",
      ja: "毎月の無料枠あり",
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
    enabled: true,
    label: {
      en: 'Hobby plan',
      'zh-Hans': 'Hobby 套餐',
      'zh-Hant': 'Hobby 方案',
      es: "Plan Hobby",
      fr: "Offre Hobby",
      de: "Hobby-Plan",
      ko: "Hobby 플랜",
      ja: "Hobby プラン",
    },
  },
} as const;
