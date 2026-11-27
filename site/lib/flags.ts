import { releaseBranch, repository } from './urls';

export const deployProviders: Record<string, { enabled: boolean; name: string; href?: string }> = {
  render: {
    enabled: true,
    name: 'Render',
    href: `https://render.com/deploy?repo=${encodeURIComponent(`${repository}/tree/${releaseBranch}`)}`,
  },
  fly: { enabled: false, name: 'Fly.io' },
  koyeb: { enabled: false, name: 'Koyeb' },
};
