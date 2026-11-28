import { promptFiles, readPrompt } from '@/lib/prompts';

export const dynamic = 'force-static';
export const dynamicParams = false;

export function generateStaticParams() {
  return promptFiles().map((name) => ({ name }));
}

export async function GET(_request: Request, { params }: { params: Promise<{ name: string }> }) {
  const { name } = await params;
  if (!promptFiles().includes(name)) return new Response(null, { status: 404 });
  return new Response(`${readPrompt(name.slice(0, -3))}\n`, {
    headers: { 'content-type': 'text/markdown; charset=utf-8' },
  });
}
