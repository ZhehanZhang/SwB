export const siteUrl = 'https://swb.one';
export const repository = 'https://github.com/ZhehanZhang/SwB';
export const releaseBranch = 'release/1.0';

export function promptUrl(file: string) {
  return `${siteUrl}/prompts/${encodeURIComponent(file)}`;
}
