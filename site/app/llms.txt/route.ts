import { locales } from '@/lib/i18n';
import { markdownResponse, siteUrl, source } from '@/lib/source';

export const dynamic = 'force-static';

export function GET() {
  const sections = locales.map((lang) => {
    const entries = source.getPages(lang).map(({ data, url }) =>
      `- [${data.title}](${siteUrl}${url})${data.description ? `: ${data.description}` : ''}`);
    return `## ${lang}\n\n${entries.join('\n')}`;
  });
  return markdownResponse(`# SwB\n\n${sections.join('\n\n')}\n`, 'text/plain');
}
