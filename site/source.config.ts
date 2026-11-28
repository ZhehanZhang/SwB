import { defineConfig, defineDocs } from 'fumadocs-mdx/config';
import { replaceSource } from 'fumadocs-core/mdx-plugins/stringifier';
import remarkCjkFriendly from 'remark-cjk-friendly';
import { isDocsVersion } from './lib/docs-version';
import { locales } from './lib/i18n';
import { readPromptFile } from './lib/prompt-source';
import { promptUrl } from './lib/urls';

type Node = Parameters<typeof replaceSource>[1];
type File = Parameters<typeof replaceSource>[0];

function remarkDocSource() {
  const walk = (node: Node, file: File) => {
    if (!('children' in node)) return;
    const children: Node[] = node.children;
    node.children = children.flatMap((child) => {
      if (child.type === 'mdxJsxFlowElement' && child.name === 'SetupPrompt') {
        const name = child.attributes.find((attr) => attr.type === 'mdxJsxAttribute' && attr.name === 'name')?.value;
        if (typeof name !== 'string') throw new Error('SetupPrompt requires a prompt name');
        const text = readPromptFile(`${name}.md`).trim();
        const fence = '`'.repeat(Math.max(3, ...Array.from(text.matchAll(/`+/g), ([run]) => run.length + 1)));
        replaceSource(file, child, `${promptUrl(`${name}.md`)}\n\n${fence}\n${text}\n${fence}`, 'markdown');
      }
      walk(child, file);
      return [child];
    }) as typeof node.children;
  };
  return walk;
}

export const docs = defineDocs({
  dir: '../docs',
  meta: { files: locales.map((locale) => `${locale}/**/{*.{json,yaml},meta{,.*}.{js,ts}}`) },
  docs: {
    files: locales.map((locale) => `${locale}/**/*.{md,mdx}`),
    postprocess: {
      includeProcessedMarkdown: {
        output: 'string',
        filterElement: (node) => !((node.type === 'mdxFlowExpression' || node.type === 'mdxTextExpression')
          && /^\s*\/\*/.test(node.value) && node.data?.estree?.body.length === 0),
      },
    },
  },
});

const versionedFolders = [
  'docs/versions',
  ...(process.env.SWB_TEST_DOCS_VERSION === '1' ? ['site/tests/fixtures/docs-versions'] : []),
];
const versionedLocales = versionedFolders.flatMap((folder) => locales.map((locale) => `${folder}/*/${locale}`));

export const versions = defineDocs({
  dir: '..',
  meta: { files: versionedLocales.map((path) => `${path}/**/{*.{json,yaml},meta{,.*}.{js,ts}}`) },
  docs: {
    postprocess: docs.docs.postprocess,
    files: versionedLocales.map((path) => `${path}/**/*.{md,mdx}`),
  },
});

const docsLink = new RegExp(`^(/(?:${locales.join('|')}))?/(docs|about)(?=/|[?#]|$)(.*)$`);

function remarkVersionLinks() {
  return (node: Node, file: File) => {
    const version = /\/(?:docs\/versions|tests\/fixtures\/docs-versions)\/([^/]+)\//.exec(file.path)?.[1];
    if (!version || !isDocsVersion(version)) return;

    function versionHref(href: string) {
      const match = docsLink.exec(href);
      if (!match || /\.mdx?(?:[?#]|$)/.test(href)) return href;
      const [, prefix = '', section, suffix] = match;
      if (section === 'docs' && /^\/v\d/.test(suffix)) return href;
      return `${prefix}/docs/v${version}${section === 'about' ? '/about' : ''}${suffix}`;
    }

    function walk(child: Node) {
      if (child.type === 'link') child.url = versionHref(child.url);
      if (child.type === 'mdxJsxFlowElement' || child.type === 'mdxJsxTextElement') {
        for (const attr of child.attributes) {
          if (attr.type === 'mdxJsxAttribute' && attr.name === 'href' && typeof attr.value === 'string') {
            attr.value = versionHref(attr.value);
          }
        }
      }
      if ('children' in child) child.children.forEach(walk);
    }
    walk(node);
  };
}

export default defineConfig({
  mdxOptions: {
    rehypeCodeOptions: {
      langs: ['shellscript', 'json', 'python', 'toml', 'yaml'],
      lazy: false,
    },
    remarkPlugins: (plugins) => [remarkDocSource, remarkVersionLinks, remarkCjkFriendly, ...plugins],
  },
});
