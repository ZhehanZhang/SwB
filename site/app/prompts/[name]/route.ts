import { readdirSync } from 'node:fs';
import { join } from 'node:path';
import { readPrompt } from '@/components/setup-prompt';

export const dynamic = 'force-static';

export function generateStaticParams() {
  return readdirSync(join(process.cwd(), '..', 'docs', 'prompts'))
    .filter((file) => file.endsWith('.md'))
    .map((file) => ({ name: file }));
}

export async function GET(_request: Request, { params }: { params: Promise<{ name: string }> }) {
  const { name } = await params;
  return new Response(`${readPrompt(name.replace(/\.md$/, ''))}\n`, {
    headers: { 'content-type': 'text/markdown; charset=utf-8' },
  });
}
