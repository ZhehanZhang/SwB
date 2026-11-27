import type { MDXComponents } from 'mdx/types';
import { Callout } from 'fumadocs-ui/components/callout';
import { Card, Cards } from 'fumadocs-ui/components/card';
import { Step, Steps } from 'fumadocs-ui/components/steps';
import { Tab as FumadocsTab, Tabs, type TabProps } from 'fumadocs-ui/components/tabs';
import defaults from 'fumadocs-ui/mdx';
import { SetupPrompt } from '@/components/setup-prompt';

function Tab(props: TabProps) {
  return <FumadocsTab {...props} forceMount />;
}

export function useMDXComponents(overrides?: MDXComponents): MDXComponents {
  return { ...defaults, Tabs, Tab, Steps, Step, Callout, Cards, Card, SetupPrompt, ...overrides };
}
