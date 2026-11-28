import assert from 'node:assert/strict';
import { locales, localize } from '../lib/i18n.ts';

const [release, candidate] = process.argv.slice(2);
if (!release || !candidate) throw new Error('Usage: node scripts/check-docs-compat.mjs RELEASE_URL CANDIDATE_URL');

const localePrefix = new RegExp(`^/(?:${locales.join('|')})(?=/)`);
const docsPath = /^\/(?:docs(?:\/|$)|about$)/;
const snapshotPath = /^\/docs\/v\d+\.\d+(?:\.\d+)?(?:\/|$)/;
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
assert.deepEqual(current, original);
const paths = [...original, ...original.filter((path) => !localePrefix.test(path)).map((path) => `/en${path}`)];
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
  const [before, after] = await Promise.all([read(release, path, /text\/html/), read(candidate, path, /text\/html/)]);
  assert.deepEqual(metadata(after), metadata(before), path);
});
await crawl(textPaths, async (path) => {
  const type = path.endsWith('.mdx') ? /text\/markdown/ : /text\/plain/;
  const [before, after] = await Promise.all([read(release, path, type), read(candidate, path, type)]);
  assert.equal(after, before, path);
});

const redirectPaths = ['/docs/about', '/docs/trademarks'].flatMap((path) =>
  [`/en${path}`, ...locales.map((lang) => localize(lang, path))]);
await crawl(redirectPaths, async (path) => {
  async function redirect(origin) {
    const url = new URL(path, origin);
    const response = await fetch(url, { headers, redirect: 'manual', signal: AbortSignal.timeout(15_000) });
    const location = response.headers.get('location');
    const target = location && new URL(location, url);
    await response.body?.cancel();
    return { status: response.status, path: target && `${target.pathname}${target.search}${target.hash}` };
  }
  const [before, after] = await Promise.all([redirect(release), redirect(candidate)]);
  assert.deepEqual(after, before, path);
});
console.log(`Matched ${original.length} canonical docs URLs, ${paths.length - original.length} English aliases, ${textPaths.length} text URLs and ${redirectPaths.length} redirect or compatibility paths.`);
