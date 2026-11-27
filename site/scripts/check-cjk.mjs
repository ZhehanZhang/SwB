#!/usr/bin/env node
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const root = new URL('../.next/server/app/', import.meta.url).pathname;
const pattern = /\*\*|__|`|\]\(/;

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? files(path) : path.endsWith('.html') ? [path] : [];
  });
}

let problems = 0;
for (const lang of ['zh-Hans', 'zh-Hant']) {
  for (const file of files(join(root, lang))) {
    const html = readFileSync(file, 'utf8');
    const main = html.match(/<article[\s\S]*?<\/article>/)?.[0] ?? html.match(/<main[\s\S]*?<\/main>/)?.[0] ?? '';
    const text = main
      .replace(/<pre[\s\S]*?<\/pre>/g, '')
      .replace(/<code[\s\S]*?<\/code>/g, '')
      .replace(/<script[\s\S]*?<\/script>/g, '')
      .replace(/<[^>]+>/g, ' ')
      .replace(/&#x27;|&quot;|&amp;/g, ' ');
    const hit = text.match(pattern);
    if (hit) {
      problems += 1;
      const at = text.indexOf(hit[0]);
      console.log(`${relative(root, file)} ${text.slice(Math.max(0, at - 40), at + 40).replace(/\s+/g, ' ')}`);
    }
  }
}
console.log(problems ? `${problems} pages with raw markdown` : 'CJK render check passed');
process.exit(problems ? 1 : 0);
