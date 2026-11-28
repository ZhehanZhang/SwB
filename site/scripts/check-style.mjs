#!/usr/bin/env node
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { locales } from '../lib/i18n.ts';

const root = fileURLToPath(new URL('../../docs/', import.meta.url));
const words = (list) => new RegExp(`(?<!\\p{L})(${list})(?!\\p{L})`, 'iu');
const negation = {
  en: /\b(no|not|never|without|none|nothing|nobody|cannot|can't|don't|doesn't|isn't|aren't|won't|wasn't|shouldn't)\b/i,
  zh: /(不|没有|沒有|无法|無法|未|勿|别|別|非|无须|無須|毋)/,
  es: words('no|nunca|jamás|sin|ningún|ninguna|ninguno|nada|nadie|tampoco'),
  fr: /(?<!\p{L})(ne|pas|jamais|sans|aucun|aucune|rien)(?!\p{L})|(?<!\p{L})n['’]/iu,
  de: words('nicht|kein|keine|keinen|keinem|keiner|keines|nie|niemals|ohne|nichts|niemand'),
  ko: /(않|없|못하|못 |말고|마세요|금지)/,
  ja: /(ない|ません|なし|無し|せず|禁止)/,
};
const punct = /[:;—–·：；]/;
const listLine = /^\s*([-*+]\s|\d+[.)]\s)/;
const listElement = /^\s*<(Steps?|[ou]l|li)\b/;

const allowedNegation = [
  ['de/install.mdx', 'ohne Anmeldung'],
  ['ko/config.mdx', '가 없으면'],
  ['ko/tools.mdx', '존재하지 않는 ID'],
  ['zh-Hans/tools.mdx', 'ID 不存在'],
  ['ko/setup/muse.mdx', '읽지 않은'],
  ['ko/tools.mdx', '읽지 않은'],
  ['ja/blocks/webhooks.mdx', '存在しない'],
  ['ja/memory.mdx', '一致するプロジェクトがない'],
  ['ja/tools.mdx', '存在しない'],
  ['zh-Hans/tools.mdx', '目标不存在'],
  ['zh-Hans/blocks/webhooks.mdx', '目标不存在'],
  ['zh-Hant/tools.mdx', '目標不存在'],
  ['zh-Hant/blocks/webhooks.mdx', '目標不存在'],
];

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? files(path) : path.endsWith('.mdx') ? [path] : [];
  });
}

let problems = 0;
for (const file of locales.flatMap((locale) => files(join(root, locale)))) {
  const lang = relative(root, file).split('/')[0];
  const zh = lang.startsWith('zh');
  const rule = zh ? negation.zh : negation[lang] ?? negation.en;
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
    const prose = zh ? line.replace(/(分別|分别|識別|识别|類別|类别|特別|特别|別名|别名|區別|区别|個別|个别|不同|不斷|不断|不久|不過|不过|非常|未來|未来|未读|未讀|無論|无论)/g, '') : line;
    const allowed = allowedNegation.filter(([path]) => relative(root, file) === path).reduce((text, [, phrase]) => text.replaceAll(phrase, ''), prose);
    if (rule.test(allowed)) found.push('negation');
    if (found.length) {
      problems += 1;
      console.log(`${relative(root, file)}:${index + 1} ${found.join(', ')}  ${raw.trim().slice(0, 120)}`);
    }
  });
}
console.log(problems ? `${problems} lines to review` : 'Style check passed');
process.exit(problems ? 1 : 0);
