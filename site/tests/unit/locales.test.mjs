import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import GithubSlugger from 'github-slugger';

const site = new URL('../../', import.meta.url).pathname;
const docs = join(site, '..', 'docs');
const locales = [...readFileSync(join(site, 'lib/i18n.ts'), 'utf8').match(/locales = \[([^\]]*)\]/)[1].matchAll(/'([^']+)'/g)].map((match) => match[1]);

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? files(path) : [relative(docs, path).split('/').slice(1).join('/')];
  });
}

const english = files(join(docs, 'en')).sort();
const read = (lang, file) => readFileSync(join(docs, lang, file), 'utf8');
const prose = (text) => text.replace(/^(```|~~~)[\s\S]*?^\1/gm, '');
const prompts = (text) => [...text.matchAll(/<SetupPrompt name="([^"]+)"/g)].map((match) => match[1]);
const images = (text) => [...text.matchAll(/!\[[^\]]*\]\(([^)\s]+)/g)].map((match) => match[1]);

function anchors(text) {
  const slugger = new GithubSlugger();
  return new Set([...prose(text).matchAll(/^#{1,6}\s+(.+?)\s*$/gm)].map(([, heading]) => {
    const custom = heading.match(/\[#([^\]]+)\]$/);
    return custom ? custom[1] : slugger.slug(heading.replace(/`/g, '').replace(/\[([^\]]*)\]\([^)]*\)/g, '$1'));
  }));
}

function target(lang, path) {
  const prefix = lang === 'en' ? '' : `/${lang}`;
  const page = path === `${prefix}/about` ? 'about' : path === `${prefix}/docs` || path.startsWith(`${prefix}/docs/`) ? path.slice(`${prefix}/docs`.length).replace(/^\//, '') : undefined;
  if (page === undefined) return undefined;
  return [`${page || 'index'}.mdx`, `${page}/index.mdx`].find((file) => existsSync(join(docs, lang, file))) ?? null;
}

test('every locale has every English docs file', () => {
  for (const lang of locales) {
    assert.ok(existsSync(join(docs, lang)), `docs/${lang} exists`);
    assert.deepEqual(files(join(docs, lang)).sort(), english, `${lang} has the English file set`);
  }
});

test('locale sidebars keep the English pages and section breaks in order', () => {
  const structure = (pages) => pages.map((entry) => {
    const label = /^---(.*)---$/.exec(entry)?.[1];
    if (label === undefined) return entry;
    assert.ok(label.trim(), 'named sidebar separators have a label');
    return '------';
  });
  for (const lang of locales) {
    for (const file of english.filter((name) => name.endsWith('meta.json'))) {
      assert.deepEqual(structure(JSON.parse(read(lang, file)).pages), structure(JSON.parse(read('en', file)).pages), `${lang}/${file}`);
    }
  }
});

test('all Deploy pages keep the bookmark anchors', () => {
  for (const lang of locales) {
    const deploy = anchors(read(lang, 'deploy.mdx'));
    for (const anchor of ['docker-compose', 'cloudflare-tunnel', 'railway', 'fly']) {
      assert.ok(deploy.has(anchor), `${lang}/deploy.mdx is missing #${anchor}`);
    }
  }
});

test('locale pages keep the English prompts and images', () => {
  for (const lang of locales) {
    for (const file of english.filter((name) => name.endsWith('.mdx'))) {
      assert.deepEqual(prompts(read(lang, file)), prompts(read('en', file)), `${lang}/${file} setup prompts`);
      assert.deepEqual(images(read(lang, file)), images(read('en', file)), `${lang}/${file} images`);
    }
  }
});

test('locale docs links stay in their locale and resolve', () => {
  const localized = new RegExp(`^/(${locales.filter((lang) => lang !== 'en').join('|')})(/|$)`);
  for (const lang of locales) {
    for (const file of english.filter((name) => name.endsWith('.mdx'))) {
      for (const [, path, hash] of prose(read(lang, file)).matchAll(/\]\((\/[^)#\s]*)(?:#([^)\s]+))?\)/g)) {
        if (path === `/${lang}`) continue;
        const page = target(lang, path);
        if (page === undefined) {
          assert.ok(!/^\/(docs|about)(\/|$)/.test(path) && !localized.test(path), `${lang}/${file} links to ${path} outside its locale`);
          continue;
        }
        assert.ok(page, `${lang}/${file} links to missing page ${path}`);
        if (hash) assert.ok(anchors(read(lang, page)).has(decodeURIComponent(hash)), `${lang}/${file} links to missing anchor ${path}#${hash}`);
      }
    }
  }
});
