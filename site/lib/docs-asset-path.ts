export const docsAssetPath = '/cdn-cgi/swb-docs';

export const prerenderedDocsRoutes = {
  'app/[lang]/(docs)/about/page': '/[lang]/about',
  'app/[lang]/(docs)/docs/[[...slug]]/page': '/[lang]/docs/[[...slug]]',
  'app/[lang]/llms-full.txt/route': '/[lang]/llms-full.txt',
  'app/cdn-cgi/swb-docs/[asset]/route': `${docsAssetPath}/[asset]`,
  'app/llms-full.txt/route': '/llms-full.txt',
  'app/llms.mdx/[...slug]/route': '/llms.mdx/[...slug]',
  'app/llms.txt/route': '/llms.txt',
  'app/sitemap.xml/route': '/sitemap.xml',
};
