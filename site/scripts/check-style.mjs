#!/usr/bin/env node
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { locales } from '../lib/i18n.ts';

const root = fileURLToPath(new URL('../../docs/', import.meta.url));
const negation = {
  en: /\b(no|not|never|without|none|nothing|nobody|cannot|can't|don't|doesn't|isn't|aren't|won't|wasn't|shouldn't)\b/i,
  zh: /(不|没有|沒有|无法|無法|未|勿|别|別|非|无须|無須|毋)/,
};
const punct = /[:;—–·：；]/;
const listLine = /^\s*([-*+]\s|\d+[.)]\s)/;
const listElement = /^\s*<(Steps?|[ou]l|li)\b/;
const legalQuote = /^\s*> (\[Section 6 of the Apache License 2\.0\]|This project is separate from SwB\.|\[Apache 许可证 2\.0 第 6 条\]|本项目独立于 SwB。|\[Apache 2\.0 授權條款第 6 條\]|本專案獨立於 SwB。)/;

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? files(path) : path.endsWith('.mdx') ? [path] : [];
  });
}

let problems = 0;
for (const file of locales.flatMap((locale) => files(join(root, locale)))) {
  const zh = /\/zh-Han[st]\//.test(file);
  const trademarks = file.endsWith('/trademarks.mdx');
  let fence = false;
  let front = 0;
  readFileSync(file, 'utf8').split('\n').forEach((raw, index) => {
    if (raw.trim() === '---' && front < 2) { front += 1; return; }
    if (front < 2) {
      const value = raw.match(/^(title|description):\s*(.*)$/)?.[2];
      if (!value) return;
      raw = value;
    }
    if (/^\s*(```|~~~)/.test(raw)) { fence = !fence; return; }
    if (fence || (/^\s*</.test(raw) && !listElement.test(raw)) || /^\s*!\[/.test(raw)) return;
    const line = raw.replace(/`[^`]*`/g, '').replace(/\]\([^)]*\)/g, ']').replace(/https?:\/\/\S+/g, '');
    const found = [];
    if (listLine.test(raw) || listElement.test(raw)) found.push('list');
    if (punct.test(line)) found.push('punctuation');
    const prose = zh ? line.replace(/(分別|分别|識別|识别|類別|类别|特別|特别|別名|别名|區別|区别|個別|个别|不同|不斷|不断|不久|不過|不过|非常|未來|未来|無論|无论)/g, '') : line;
    const allowed = (trademarks && legalQuote.test(raw)) || (zh && raw.includes('{/* negation-ok */}'));
    if ((zh ? negation.zh : negation.en).test(prose) && !allowed) found.push('negation');
    if (found.length) {
      problems += 1;
      console.log(`${relative(root, file)}:${index + 1} ${found.join(', ')}  ${raw.trim().slice(0, 120)}`);
    }
  });
}
console.log(problems ? `${problems} lines to review` : 'Style check passed');
process.exit(problems ? 1 : 0);
