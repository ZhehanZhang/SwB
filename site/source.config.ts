import { defineConfig, defineDocs } from 'fumadocs-mdx/config';
import { replaceSource } from 'fumadocs-core/mdx-plugins/stringifier';
import remarkCjkFriendly from 'remark-cjk-friendly';
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

export default defineConfig({
  mdxOptions: {
    rehypeCodeOptions: {
      langs: ['shellscript', 'json', 'python', 'toml', 'yaml'],
      lazy: false,
    },
    remarkPlugins: (plugins) => [remarkDocSource, remarkCjkFriendly, ...plugins],
  },
});
