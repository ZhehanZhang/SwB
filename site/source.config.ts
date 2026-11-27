import { defineConfig, defineDocs } from 'fumadocs-mdx/config';
import remarkCjkFriendly from 'remark-cjk-friendly';

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
    remarkPlugins: (plugins) => [remarkCjkFriendly, ...plugins],
  },
});
