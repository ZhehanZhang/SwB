export const latestDocsVersion = '1.0';

export function isDocsVersion(value: string) {
  return /^(?:0|[1-9]\d{0,5})\.(?:0|[1-9]\d{0,5})(?:\.(?:0|[1-9]\d{0,5}))?$/.test(value);
}

export function splitDocsSlug([first, ...rest]: string[] = []) {
  if (first?.startsWith('v') && isDocsVersion(first.slice(1))) {
    return { version: first.slice(1), slug: rest };
  }
  return { version: latestDocsVersion, slug: first === undefined ? [] : [first, ...rest] };
}

export function docsVersionAtPath(path: string) {
  const [, first, second, third] = path.split('/');
  const segment = first === 'docs' ? second : second === 'docs' ? third : undefined;
  return splitDocsSlug(segment ? [segment] : []).version;
}

export function docsRoot(version: string) {
  return version === latestDocsVersion ? '/docs' : `/docs/v${version}`;
}

export function docsAssetName(version: string, name: string) {
  return `${version === latestDocsVersion ? '' : `v${version}-`}${name}.json`;
}
