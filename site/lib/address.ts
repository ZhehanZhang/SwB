export const swbAddressKey = 'swb-address';

const authorityPattern = /^https?:\/\/(\[[\da-f:.]+\]|[a-z\d.-]+)(?::\d+)?(?:[/?#]|$)/i;
const dnsLabel = /^[a-z\d](?:[a-z\d-]{0,61}[a-z\d])?$/i;
const loopback = new Set(['localhost', '127.0.0.1', '[::1]']);

export function validSwbOrigin(value: string | null): string | null {
  if (!value || value.length > 253 || /[^!-~]|[<>"'`\\]/.test(value)) return null;
  const authority = value.match(authorityPattern);
  if (!authority) return null;

  try {
    const url = new URL(value);
    const host = authority[1].toLowerCase();
    if (!host.startsWith('[') && (host !== url.hostname || !host.replace(/\.$/, '').split('.').every((label) => dnsLabel.test(label)))) {
      return null;
    }
    if (url.protocol === 'http:' && !loopback.has(url.hostname)) return null;
    return url.origin;
  } catch {
    return null;
  }
}

export function readSharedAddress(url: URL) {
  const hash = url.hash.slice(1);
  const fragments = [...hash.matchAll(/(?:^|&)swb=([^&#]*)/g)];
  const fromHash = fragments.map(([, raw]) => {
    try {
      return validSwbOrigin(decodeURIComponent(raw));
    } catch {
      return null;
    }
  }).find(Boolean);
  const address = fromHash ?? validSwbOrigin(url.searchParams.get('swb'));
  if (!address) return null;

  if (fragments.length) url.hash = hash.replace(/(?:^|&)swb=[^&#]*/g, '').replace(/^[&#]/, '');
  if (url.searchParams.has('swb')) url.searchParams.delete('swb');
  return address;
}

export function linkWithSwbAddress(href: string, address: string) {
  const url = new URL(href);
  if (url.searchParams.has('swb')) url.searchParams.delete('swb');
  if (isGetStartedAddressPath(url.pathname) && url.searchParams.has('url')) url.searchParams.delete('url');
  const hash = url.hash.slice(1).split('&').filter((part) => part && !part.startsWith('swb='));
  url.hash = [...hash, `swb=${encodeURIComponent(address)}`].join('&');
  return url.href;
}

export function isGetStartedAddressPath(path: string) {
  return /\/docs\/get-started\/?$/.test(path);
}

export function isAddressDocsPath(path: string) {
  return /^\/(?:en\/|zh-Hans\/|zh-Hant\/)?docs(?:\/|$)/.test(path);
}
