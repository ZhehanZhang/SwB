import FumadocsLink from 'fumadocs-core/link';
import defaults from 'fumadocs-ui/mdx';
import type { ComponentProps } from 'react';
import { PersonalizedCodeBlock, PersonalizedInlineCode, PersonalizedLink } from '@/components/personalized-docs';
import { codeText } from './mdx-text';
import { exampleSwbOrigins, hasSwbExample } from './urls';

export function MdxCode(props: ComponentProps<'code'>) {
  if (typeof props.children === 'string' && hasSwbExample(props.children)) {
    return <PersonalizedInlineCode {...props} children={props.children} />;
  }
  return <code {...props} />;
}

export function MdxPre(props: ComponentProps<typeof defaults.pre>) {
  return hasSwbExample(codeText(props.children)) ? <PersonalizedCodeBlock {...props} /> : <defaults.pre {...props} />;
}

export function MdxLink(props: ComponentProps<typeof FumadocsLink>) {
  const { href } = props;
  if (href && exampleSwbOrigins.some((origin) => href.startsWith(origin) && /^(?:[/?#]|$)/.test(href.slice(origin.length)))) {
    return <PersonalizedLink {...props} />;
  }
  return <FumadocsLink {...props} />;
}
