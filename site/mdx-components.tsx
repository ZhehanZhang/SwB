import type { MDXComponents } from 'mdx/types';
import { Callout } from 'fumadocs-ui/components/callout';
import { Card, Cards } from 'fumadocs-ui/components/card';
import { Step, Steps } from 'fumadocs-ui/components/steps';
import { Tab as FumadocsTab, Tabs, type TabProps } from 'fumadocs-ui/components/tabs';
import defaults from 'fumadocs-ui/mdx';
import { SetupPicker } from '@/components/setup-picker';
import { SetupPrompt } from '@/components/setup-prompt';
import { MdxCode, MdxPre } from '@/lib/mdx-address';

function Tab(props: TabProps) {
  return <FumadocsTab {...props} forceMount />;
}

export function useMDXComponents(overrides?: MDXComponents): MDXComponents {
  return { ...defaults, code: MdxCode, pre: MdxPre, Tabs, Tab, Steps, Step, Callout, Cards, Card, SetupPicker, SetupPrompt, ...overrides };
}
