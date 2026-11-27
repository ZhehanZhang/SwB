export const siteUrl = 'https://swb.one';

export function promptUrl(file: string) {
  return `${siteUrl}/prompts/${encodeURIComponent(file)}`;
}
