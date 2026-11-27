import { defineConfig, defineDocs } from 'fumadocs-mdx/config';
import { replaceSource } from 'fumadocs-core/mdx-plugins/stringifier';
import remarkCjkFriendly from 'remark-cjk-friendly';
import { readPrompt } from './lib/prompts';
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
        const text = readPrompt(name);
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
  dir: 'content/docs',
  docs: {
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
    remarkPlugins: (plugins) => [remarkDocSource, remarkCjkFriendly, ...plugins],
  },
});
