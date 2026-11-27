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
    href: 'https://railway.com/deploy/swb',
  },
};
