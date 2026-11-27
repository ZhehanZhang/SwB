import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

const directory = join(process.cwd(), '..', 'docs', 'prompts');

export function promptSourceFiles() {
  return readdirSync(directory, { withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.endsWith('.md'))
    .map((entry) => entry.name)
    .sort();
}

export function readPromptFile(file: string) {
  return readFileSync(join(directory, file), 'utf8');
}
