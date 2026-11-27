#!/usr/bin/env node
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const root = new URL('../content/docs/', import.meta.url).pathname;
const negation = {
  en: /\b(no|not|never|without|none|nothing|nobody|cannot|can't|don't|doesn't|isn't|aren't|won't|wasn't|shouldn't)\b/i,
  zh: /(不|没有|沒有|无法|無法|未|勿|别|別|非|无须|無須|毋)/,
};
const punct = /(:\s|;|—|–|·|：|；|——)/;
const listLine = /^\s*([-*+]\s|\d+[.)]\s)/;

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? files(path) : path.endsWith('.mdx') ? [path] : [];
  });
}

let problems = 0;
for (const file of files(root)) {
  const zh = /\/zh-Han[st]\//.test(file);
  let fence = false;
  let front = 0;
  readFileSync(file, 'utf8').split('\n').forEach((raw, index) => {
    if (raw.trim() === '---' && front < 2) { front += 1; return; }
    if (front < 2) return;
    if (/^\s*(```|~~~)/.test(raw)) { fence = !fence; return; }
    if (fence || /^\s*</.test(raw) || /^\s*!\[/.test(raw)) return;
    const line = raw.replace(/`[^`]*`/g, '').replace(/\]\([^)]*\)/g, ']').replace(/https?:\/\/\S+/g, '');
    const found = [];
    if (listLine.test(raw)) found.push('list');
    if (punct.test(line)) found.push('punctuation');
    if ((zh ? negation.zh : negation.en).test(line.replace(/^\s*>.*$/, ''))) found.push('negation');
    if (found.length) {
      problems += 1;
      console.log(`${relative(root, file)}:${index + 1} ${found.join(', ')}  ${raw.trim().slice(0, 120)}`);
    }
  });
}
console.log(problems ? `${problems} lines to review` : 'Style check passed');
process.exit(problems ? 1 : 0);
