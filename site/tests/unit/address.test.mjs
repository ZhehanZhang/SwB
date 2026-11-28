import assert from 'node:assert/strict';
import { test } from 'node:test';
import { linkWithSwbAddress, readSharedAddress, validSwbOrigin } from '../../lib/address.ts';
import { exampleSwbOrigins, personalizeSwb } from '../../lib/urls.ts';

test('normalizes public HTTPS origins and local HTTP origins', () => {
  for (const [input, expected] of [
    ['https://SwB.Example.net:9443/mcp?secret=sample#anchor', 'https://swb.example.net:9443'],
    ['HTTPS://example.org:443/', 'https://example.org'],
    ['https://xn--caf-dma.example/', 'https://xn--caf-dma.example'],
    ['https://192.0.2.1/', 'https://192.0.2.1'],
    ['https://[2001:db8::42]:8443/', 'https://[2001:db8::42]:8443'],
    ['http://localhost:8870/mcp', 'http://localhost:8870'],
    ['http://127.0.0.1:80', 'http://127.0.0.1'],
    ['http://[0:0:0:0:0:0:0:1]:8080/mcp', 'http://[::1]:8080'],
    [`https://example.com/${'a'.repeat(233)}`, 'https://example.com'],
  ]) assert.equal(validSwbOrigin(input), expected, input);
});

test('rejects hostile and ambiguous origins', () => {
  for (const input of [
    null, '', 'javascript:alert(1)', 'data:text/html,alert(1)', 'file:///tmp/file', 'ftp://example.com',
    'https://user:pw@x.com', 'https://@x.com', 'https://x.com"><script>', 'https://%78.com',
    'https://swb。example.com', 'https://café.example', 'https://example.com\u202e',
    ' https://example.com', 'https://example.com/a b', 'https://x.co\tm', 'https://x.com\u007f',
    'https://example.com\\@localhost', 'https:///example.com', 'https://-example.com',
    'https://example-.com', 'https://example..com', 'https://under_score.com', 'https://[::not-an-ip]',
    'https://999.0.0.1', 'https://0x7f000001', 'http://127.1', 'http://2130706433',
    'http://example.com', 'http://localhost.example', 'http://127.0.0.2', 'http://localhost.', 'http://[::2]',
    `https://example.com/${'a'.repeat(234)}`, `https://${'a'.repeat(64)}.example`, 'https://example.com:65536',
  ]) assert.equal(validSwbOrigin(input), null, String(input));
});

test('strips a valid share from the URL and preserves other parameters and anchors', () => {
  const address = 'https://test.example.net:9443';
  const location = new URL('https://swb.one/docs/install?tab=docker#check-the-server');
  const shared = new URL(linkWithSwbAddress(location.href, address));
  assert.equal(shared.searchParams.has('swb'), false);
  assert.match(shared.hash, /^#check-the-server&swb=https%3A/);
  assert.equal(readSharedAddress(shared), address);
  assert.equal(shared.href, location.href);

  const query = new URL(`${location.origin}${location.pathname}?tab=docker&swb=${encodeURIComponent(address + '/mcp')}${location.hash}`);
  assert.equal(readSharedAddress(query), address);
  assert.equal(query.href, location.href);

  const invalid = new URL(location);
  invalid.hash = '#swb=javascript%3Aalert(1)&check-the-server';
  const original = invalid.href;
  assert.equal(readSharedAddress(invalid), null);
  assert.equal(invalid.href, original);
});

test('replaces only canonical SwB examples and retains host-only syntax', () => {
  const origin = 'https://test.example.net:9443';
  const [primary, secondary] = exampleSwbOrigins;
  const text = `${primary}/mcp ${secondary}/healthz swb.example.com https://not-swb.example.com https://swb.example.com.attacker.example http://localhost:8870`;
  assert.equal(personalizeSwb(text, origin), `${origin}/mcp ${origin}/healthz test.example.net:9443 https://not-swb.example.com https://swb.example.com.attacker.example http://localhost:8870`);
  assert.equal(personalizeSwb(text, null), text);
});
