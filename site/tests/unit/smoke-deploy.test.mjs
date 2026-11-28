import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { once } from 'node:events';
import { createServer } from 'node:http';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

const script = fileURLToPath(new URL('../../scripts/smoke-deploy.mjs', import.meta.url));
const html = (lang, title, heading = `<h1>${title}</h1>`) => [
  'text/html',
  `<html lang="${lang}"><title>${title}</title>${heading}</html>`,
];
const pages = {
  '/docs/': html('en', 'SwB'),
  '/docs/setup/mcp-client': html('en', 'MCP client'),
  '/zh-Hans/docs': html('zh-Hans', 'SwB'),
  '/zh-Hans/docs/setup/mcp-client': html('zh-Hans', 'MCP 客户端'),
  '/zh-Hant/docs': html('zh-Hant', 'SwB'),
  '/zh-Hant/docs/setup/mcp-client': html('zh-Hant', 'MCP 用戶端'),
  ...Object.fromEntries(Object.entries({ es: 'Cliente MCP', fr: 'Client MCP', de: 'MCP-Client', ko: 'MCP 클라이언트', ja: 'MCP クライアント' })
    .flatMap(([lang, title]) => [[`/${lang}/docs`, html(lang, 'SwB')], [`/${lang}/docs/setup/mcp-client`, html(lang, title)]])),
  '/': html('en', 'SwB', '<h1 id="landing-heading">First-party agents working together</h1>'),
  '/about': html('en', 'About'),
  '/prompts/claude.md': ['text/markdown', 'Help me connect Claude to SwB.'],
  '/llms.txt': ['text/plain', '# SwB\n\n## Prompts\n\n- [Claude](https://swb.one/prompts/claude.md)'],
};

async function startFixture(t, respond) {
  const requested = [];
  const server = createServer((request, response) => {
    const { url } = request;
    requested.push(url);
    if (respond?.(url, response)) return;
    if (url === '/docs') return response.writeHead(308, { location: '/docs/' }).end();
    const page = pages[url];
    if (!page) return response.writeHead(404).end();
    const [type, body] = page;
    response.writeHead(200, { 'content-type': `${type}; charset=utf-8` }).end(body);
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const origin = `http://127.0.0.1:${server.address().port}`;
  return { requested, run: () => promisify(execFile)(process.execPath, [script, '--base-url', origin]) };
}

test('fetches all public routes and follows redirects', async (t) => {
  const { requested, run } = await startFixture(t);
  const { stdout } = await run();
  assert.match(stdout, /passed for 20 routes/);
  assert.deepEqual(requested.sort(), ['/docs', ...Object.keys(pages)].sort());
});

test('fails on redirected errors, locale fallback, wrong titles, and missing plaintext markers', async (t) => {
  const { run } = await startFixture(t, (url, response) => {
    if (url === '/about') return response.writeHead(302, { location: '/gone' }).end();
    if (url === '/gone') return response.writeHead(503).end();
    if (url === '/zh-Hans/docs') {
      return response.writeHead(200, { 'content-type': 'text/html' }).end(pages['/docs/'][1]);
    }
    if (url === '/zh-Hant/docs/setup/mcp-client') {
      return response.writeHead(200, { 'content-type': 'text/html' }).end(pages['/zh-Hant/docs'][1]);
    }
    if (url === '/prompts/claude.md') {
      return response.writeHead(200, { 'content-type': 'text/markdown' }).end('Different prompt');
    }
    if (url === '/llms.txt') {
      return response.writeHead(200, { 'content-type': 'text/html' }).end(pages['/llms.txt'][1]);
    }
  });

  await assert.rejects(run(), ({ code, stderr }) => {
    assert.equal(code, 1);
    assert.match(stderr, /\/about: HTTP 503 after redirect/);
    assert.match(stderr, /\/zh-Hans\/docs: missing HTML language zh-Hans/);
    assert.match(stderr, /\/zh-Hant\/docs\/setup\/mcp-client: missing title "MCP 用戶端"/);
    assert.match(stderr, /\/prompts\/claude\.md: missing body marker/);
    assert.match(stderr, /\/llms\.txt: expected content type text\/plain/);
    return true;
  });
});
