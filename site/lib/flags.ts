import { releaseBranch, repository } from './urls';

export const DEPLOY_BUTTONS_LIVE = false;

export const deployProviders = {
  render: {
    name: 'Render',
    href: DEPLOY_BUTTONS_LIVE
      ? `https://render.com/deploy?repo=${encodeURIComponent(`${repository}/tree/${releaseBranch}`)}`
      : `${repository}/blob/${releaseBranch}/deploy/README.md#render`,
  },
  railway: {
    name: 'Railway',
    enabled: process.env.NODE_ENV === 'development' && process.env.SWB_TEST_DEPLOY_RAILWAY === '1',
  },
};
