import { promptFiles } from '@/lib/prompts';
import { promptUrl } from '@/lib/urls';

export const dynamic = 'force-static';

export function GET() {
  const entries = promptFiles().map((file) => `setup-${file.slice(0, -3)} ${promptUrl(file)}`);
  return new Response(`${entries.join('\n')}\n`, {
    headers: { 'content-type': 'text/plain; charset=utf-8' },
  });
}
