export const siteUrl = 'https://swb.one';
export const repository = 'https://github.com/ZhehanZhang/SwB';
export const releaseBranch = 'release/1.0';
export const exampleSwbOrigins = ['https://swb.example.com', 'https://swb-your-team.example.net'] as const;

const exampleSwbHosts = exampleSwbOrigins.map((origin) => new URL(origin).host.replaceAll('.', '\\.')).join('|');
const exampleSwbPattern = new RegExp(`(?<![\\w./:@-])(https:\\/\\/)?(?:${exampleSwbHosts})(?![\\w@:-]|\\.[\\w-])`, 'gi');
const exampleSwbTest = new RegExp(exampleSwbPattern.source, 'i');

export function hasSwbExample(text: string) {
  return exampleSwbTest.test(text);
}

export function personalizeSwb(text: string, origin: string | null) {
  return origin ? text.replace(exampleSwbPattern, (_, scheme) => scheme ? origin : new URL(origin).host) : text;
}

export function promptUrl(file: string) {
  return `${siteUrl}/prompts/${encodeURIComponent(file)}`;
}
