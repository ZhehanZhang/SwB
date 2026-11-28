import assert from 'node:assert/strict';
import { locales, localize } from '../lib/i18n.ts';
import { siteUrl } from '../lib/urls.ts';

const [release, candidate, mode] = process.argv.slice(2);
if (!release || !candidate || (mode !== undefined && mode !== '--docs-ia')) {
  throw new Error('Usage: node scripts/check-docs-compat.mjs BASELINE_URL CANDIDATE_URL [--docs-ia]');
}

const docsIA = mode === '--docs-ia';
const localePrefix = new RegExp(`^/(?:${locales.join('|')})(?=/)`);
const docsPath = /^\/(?:docs(?:\/|$)|about$)/;
const snapshotPath = /^\/docs\/v\d+\.\d+(?:\.\d+)?(?:\/|$)/;
const moved = new Map([
  ['/docs/self-hosting', '/docs/deploy'],
  ['/docs/setup/cloudflare', '/docs/cloudflare'],
]);
const additions = ['/docs/cloudflare', '/docs/operate'];
const retired = /\/docs\/(?:self-hosting|setup\/cloudflare)(?=[#?)\s<]|\.mdx?\b|$)/;
const headers = { 'accept-language': 'en', 'user-agent': 'curl/docs-compat' };

async function read(origin, path, type) {
  const url = new URL(path, origin);
  const response = await fetch(url, { headers, redirect: 'manual', signal: AbortSignal.timeout(15_000) });
  assert.equal(response.status, 200, url.href);
  assert.match(response.headers.get('content-type') ?? '', type, url.href);
  return response.text();
}

async function latestDocs(origin) {
  const xml = await read(origin, '/sitemap.xml', /xml/);
  return [...xml.matchAll(/<loc>([^<]+)<\/loc>/g)]
    .map(([, href]) => new URL(href).pathname)
    .filter((url) => {
      const bare = url.replace(localePrefix, '');
      return docsPath.test(bare) && !snapshotPath.test(bare);
    }).sort();
}

function metadata(html) {
  return {
    lang: html.match(/<html\b[^>]*\slang="([^"]+)"/)?.[1],
    title: html.match(/<title>([^<]+)<\/title>/)?.[1],
    links: (html.match(/<link\b[^>]*>/g) ?? [])
      .filter((tag) => /\brel="canonical"|\brel="alternate"/.test(tag)).sort(),
  };
}

const [original, current] = await Promise.all([latestDocs(release), latestDocs(candidate)]);
assert.ok(original.length > 0);
const expected = docsIA
  ? [...original.filter((path) => !moved.has(path.replace(localePrefix, ''))),
    ...locales.flatMap((lang) => additions.map((path) => localize(lang, path)))].sort()
  : original;
assert.deepEqual(current, expected);
const paths = [...current, ...current.filter((path) => !localePrefix.test(path)).map((path) => `/en${path}`)];
const textPaths = [...paths.map((path) => `${path}.mdx`), '/llms.txt', '/en/llms-full.txt',
  ...locales.map((lang) => localize(lang, '/llms-full.txt'))];

async function crawl(items, compare) {
  const failures = [];
  await Promise.all(Array.from({ length: 12 }, async (_, index) => {
    for (let next = index; next < items.length; next += 12) {
      try {
        await compare(items[next]);
      } catch (error) {
        failures.push(error);
      }
    }
  }));
  if (failures.length) throw new AggregateError(failures, `${failures.length} docs URLs differ`);
}

await crawl(paths, async (path) => {
  const after = await read(candidate, path, /text\/html/);
  const bare = path.replace(localePrefix, '');
  if (docsIA && (bare === '/docs/deploy' || additions.includes(bare))) {
    const { lang, title, links } = metadata(after);
    assert.equal(lang, path.match(localePrefix)?.[0].slice(1) ?? 'en', path);
    assert.ok(title, path);
    assert.ok(links.some((link) => link.includes('rel="canonical"')
      && link.includes(`href="${siteUrl}${path.replace(/^\/en(?=\/)/, '')}"`)), path);
    for (const language of [...locales, 'x-default']) {
      const href = language === 'x-default' ? bare : localize(language, bare);
      assert.ok(links.some((link) => link.includes('rel="alternate"')
        && link.match(/\bhreflang="([^"]+)"/i)?.[1] === language
        && link.includes(`href="${siteUrl}${href}"`)), `${path} ${language}`);
    }
    if (bare === '/docs/deploy') {
      for (const anchor of ['docker-compose', 'cloudflare-tunnel', 'railway', 'fly']) {
        assert.ok(after.includes(`id="${anchor}"`), `${path}#${anchor}`);
      }
    }
    return;
  }
  const before = await read(release, path, /text\/html/);
  assert.deepEqual(metadata(after), metadata(before), path);
});
await crawl(textPaths, async (path) => {
  const type = path.endsWith('.mdx') ? /text\/markdown/ : /text\/plain/;
  const after = await read(candidate, path, type);
  if (docsIA) {
    assert.doesNotMatch(after, retired, path);
    if (path.endsWith('.mdx')) {
      assert.ok(after.includes(`\nURL: ${siteUrl}${path.replace(/^\/en(?=\/)/, '').slice(0, -4)}\n`), path);
    }
    return;
  }
  const before = await read(release, path, type);
  assert.equal(after, before, path);
});

async function redirect(origin, path) {
  const url = new URL(path, origin);
  const response = await fetch(url, { headers, redirect: 'manual', signal: AbortSignal.timeout(15_000) });
  const location = response.headers.get('location');
  const target = location && new URL(location, url);
  await response.body?.cancel();
  return { status: response.status, path: target && `${target.pathname}${target.search}${target.hash}` };
}

const redirectPaths = ['/docs/about', '/docs/trademarks'].flatMap((path) =>
  [`/en${path}`, ...locales.map((lang) => localize(lang, path))]);
await crawl(redirectPaths, async (path) => {
  const [before, after] = await Promise.all([redirect(release, path), redirect(candidate, path)]);
  assert.deepEqual(after, before, path);
});
const movedRedirects = !docsIA ? [] : [...moved].flatMap(([old, next]) =>
  ['', ...locales.map((lang) => `/${lang}`)].flatMap((prefix) => [
    [`${prefix}${old}`, `${prefix}${next}`],
    [`${prefix}${old}.md`, `${prefix}${next}.mdx`],
    [`${prefix}${old}.mdx`, `${prefix}${next}.mdx`],
    [`/llms.mdx${prefix}${old}`, `/llms.mdx${prefix === '/en' ? '' : prefix}${next}`],
  ]));
await crawl(movedRedirects, async ([path, target]) => {
  assert.deepEqual(await redirect(candidate, `${path}?ref=compat`),
    { status: 308, path: `${target}?ref=compat` }, path);
});
console.log(`Checked ${current.length} canonical docs URLs, ${paths.length - current.length} English aliases, ${textPaths.length} text URLs and ${redirectPaths.length + movedRedirects.length} redirect or compatibility paths.`);
