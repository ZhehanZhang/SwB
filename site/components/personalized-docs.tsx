'use client';

import { CodeBlock, Pre } from 'fumadocs-ui/components/codeblock';
import FumadocsLink from 'fumadocs-core/link';
import { Children, cloneElement, isValidElement, type ComponentProps, type ReactNode } from 'react';
import type { Locale } from '@/lib/i18n';
import { codeText } from '@/lib/mdx-text';
import { personalizeSwb } from '@/lib/urls';
import { CopyTextButton } from './prompt-actions';
import { useSwbAddress } from './swb-address-provider';

function personalizeCode(node: ReactNode, address: string): ReactNode {
  return Children.map(node, (child) => {
    if (typeof child === 'string') return personalizeSwb(child, address);
    if (!isValidElement<{ children?: ReactNode }>(child) || !child.props.children) return child;
    return cloneElement(child, undefined, personalizeCode(child.props.children, address));
  });
}

export function PersonalizedCodeBlock({ children, ...props }: ComponentProps<typeof CodeBlock>) {
  const { address } = useSwbAddress();
  let content = children;
  if (address) {
    content = personalizeCode(children, address);
    const plain = personalizeSwb(codeText(children), address);
    if (plain !== codeText(content)) content = <code>{plain}</code>;
  }
  return <CodeBlock {...props}><Pre>{content}</Pre></CodeBlock>;
}

function InlineText({ text, address, tabIndex }: { text: string; address: string | null; tabIndex?: number }) {
  const personalized = personalizeSwb(text, address);
  return (
    <span className="swb-inline-address" style={{ inlineSize: `${text.length}ch` }}>
      <span title={personalized} tabIndex={personalized.length > text.length ? tabIndex : undefined}>{personalized}</span>
    </span>
  );
}

export function PersonalizedInlineCode({ children, ...props }: ComponentProps<'code'> & { children: string }) {
  const { address } = useSwbAddress();
  return <code {...props}><InlineText text={children} address={address} tabIndex={0} /></code>;
}

export function PersonalizedLink({ href, children, ...props }: ComponentProps<typeof FumadocsLink>) {
  const { address } = useSwbAddress();
  return (
    <FumadocsLink href={href && personalizeSwb(href, address)} {...props}>
      {children === href && typeof children === 'string' ? <InlineText text={children} address={address} /> : children}
    </FumadocsLink>
  );
}

const markdownLabels = {
  en: { copy: 'Copy Markdown', copied: 'Copied Markdown' },
  'zh-Hans': { copy: '复制 Markdown', copied: '已复制 Markdown' },
  'zh-Hant': { copy: '複製 Markdown', copied: '已複製 Markdown' },
  es: { copy: 'Copiar Markdown', copied: 'Markdown copiado' },
  fr: { copy: 'Copier le Markdown', copied: 'Markdown copié' },
  de: { copy: 'Markdown kopieren', copied: 'Markdown kopiert' },
  ko: { copy: 'Markdown 복사', copied: 'Markdown을 복사했습니다' },
  ja: { copy: 'Markdown をコピー', copied: 'Markdown をコピーしました' },
};

export function PersonalizedMarkdownCopy({ lang, markdownUrl }: { lang: Locale; markdownUrl: string }) {
  const { address } = useSwbAddress();
  return <CopyTextButton labels={markdownLabels[lang as keyof typeof markdownLabels] ?? markdownLabels.en} className="swb-button swb-button-compact swb-doc-copy bg-fd-secondary hover:bg-fd-accent"
    text={async () => {
      const response = await fetch(markdownUrl);
      if (!response.ok) throw new Error(`Failed to fetch ${markdownUrl}`);
      return personalizeSwb(await response.text(), address);
    }} />;
}
