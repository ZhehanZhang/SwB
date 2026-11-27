import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

const directory = join(process.cwd(), '..', 'docs', 'prompts');

export function promptFiles() {
  return readdirSync(directory, { withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.endsWith('.md'))
    .map((entry) => entry.name)
    .sort();
}

export function readPromptSource(name: string) {
  return readFileSync(join(directory, `${name}.md`), 'utf8');
}

export function readPrompt(name: string) {
  return readPromptSource(name).trim();
}
