import { fullMarkdown, markdownResponse } from '@/lib/source';

export const dynamic = 'force-static';

export async function GET() {
  return markdownResponse(await fullMarkdown('en'), 'text/plain');
}
