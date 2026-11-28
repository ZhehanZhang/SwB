import { locales } from '@/lib/i18n';
import { promptFiles } from '@/lib/prompts';
import { markdownResponse, siteUrl, source } from '@/lib/source';
import { promptUrl } from '@/lib/urls';

export const dynamic = 'force-static';

export function GET() {
  const prompts = promptFiles().map((file) => {
    const name = file.slice(0, -3);
    const title = name === 'coordinator' ? 'Coordinate agents' : name === 'deploy' ? 'Deploy SwB'
      : source.getPage(['setup', name], 'en')?.data.title ?? name.replaceAll('-', ' ');
    return `- [${title}](${promptUrl(file)})`;
  });
  const sections = locales.map((lang) => {
    const entries = source.getPages(lang).map(({ data, url }) =>
      `- [${data.title}](${siteUrl}${url})${data.description ? `: ${data.description}` : ''}`);
    return `## ${lang}\n\n${entries.join('\n')}`;
  });
  return markdownResponse(`# SwB\n\n## Prompts\n\n${prompts.join('\n')}\n\n${sections.join('\n\n')}\n`, 'text/plain');
}
