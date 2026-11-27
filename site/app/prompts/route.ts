import { promptFiles } from '@/lib/prompts';
import { promptUrl } from '@/lib/urls';

export const dynamic = 'force-static';

export function GET() {
  return new Response(`${promptFiles().map(promptUrl).join('\n')}\n`, {
    headers: { 'content-type': 'text/plain; charset=utf-8' },
  });
}
