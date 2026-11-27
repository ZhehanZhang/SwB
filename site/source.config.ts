import { defineConfig, defineDocs } from 'fumadocs-mdx/config';
import remarkCjkFriendly from 'remark-cjk-friendly';
import { DEPLOY_BUTTONS_LIVE } from './lib/flags';

type Node = { type: string; name?: string; children?: Node[] };

function remarkDeployFlag() {
  const walk = (node: Node) => {
    if (!node.children) return;
    node.children = node.children.flatMap((child) => {
      if (child.type === 'mdxJsxFlowElement' && (child.name === 'DeployLive' || child.name === 'DeployHeld')) {
        return (child.name === 'DeployLive') === DEPLOY_BUTTONS_LIVE ? child.children ?? [] : [];
      }
      walk(child);
      return [child];
    });
  };
  return walk;
}

export const docs = defineDocs({
  dir: 'content/docs',
  docs: {
    postprocess: {
      includeProcessedMarkdown: true,
    },
  },
});

export default defineConfig({
  mdxOptions: {
    remarkPlugins: (plugins) => [remarkDeployFlag, remarkCjkFriendly, ...plugins],
  },
});
