import promptBundle from '../.generated/prompts.json';

const prompts: Record<string, string> = promptBundle;

export function promptFiles() {
  return Object.keys(prompts).sort();
}

export function readPromptSource(name: string) {
  const file = `${name}.md`;
  if (!Object.hasOwn(prompts, file)) throw new Error(`Unknown prompt: ${name}`);
  return prompts[file];
}

export function readPrompt(name: string) {
  return readPromptSource(name).trim();
}
