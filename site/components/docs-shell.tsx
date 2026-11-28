'use client';

import { useChat } from '@ai-sdk/react';
import { AIChatPanel, AIChatProvider, AIChatSearch, useAIChat, type AIChatClientData } from '@fumadocs/ai-chat';
import { DefaultChatTransport, type UIMessage } from 'ai';
import { usePathname } from 'fumadocs-core/framework';
import type { Separator } from 'fumadocs-core/page-tree';
import { TreeContextProvider, useTreeContext } from 'fumadocs-ui/contexts/tree';
import { DocsLayout, type DocsLayoutProps } from 'fumadocs-ui/layouts/notebook';
import {
  Sidebar, SidebarCollapseTrigger, SidebarProvider, SidebarTrigger, useSidebar, type SidebarProps,
} from 'fumadocs-ui/layouts/notebook/slots/sidebar';
import { useMemo } from 'react';
import { docsVersionAtPath, latestDocsVersion } from '@/lib/docs-version';
import { isLocale, localize, type Locale } from '@/lib/i18n';

const blocksOverview = {
  en: 'Overview',
  'zh-Hans': '概览',
  'zh-Hant': '總覽',
  es: 'Vista general',
  fr: 'Vue d’ensemble',
  de: 'Übersicht',
  ko: '개요',
  ja: '概要',
} satisfies Record<Locale, string>;

type Message = UIMessage<unknown, { client: AIChatClientData }, {
  search: {
    input: { query: string };
    output: { doc: { url: string; title: string } }[];
  };
}>;

function ChatLayout({ children, ...props }: DocsLayoutProps) {
  const { open, setOpen } = useAIChat();
  return (
    <DocsLayout {...props} aiChat={{ open, onOpenChange: setOpen, panel: <AIChatPanel /> }}>
      {children}
    </DocsLayout>
  );
}

function renderPart(part: Message['parts'][number], live: boolean) {
  return part.type === 'tool-search' ? <AIChatSearch part={part} live={live} /> : null;
}

function DocsGroup({ item }: { item: Separator }) {
  if (!item.name) return <hr className="mx-2 my-3 border-fd-border" />;
  return <p className="mt-3 px-2 py-1 text-sm font-semibold text-fd-foreground">{item.name}</p>;
}

function DocsSidebar(props: SidebarProps) {
  const { full } = useTreeContext();
  const [, prefix] = usePathname().split('/');
  const lang = isLocale(prefix) ? prefix : 'en';
  const overview = localize(lang, '/docs/blocks');
  const tree = useMemo(() => ({
    ...full,
    children: full.children.map((item) => item.type === 'page' && item.url === overview
      ? { ...item, name: blocksOverview[lang] }
      : item),
  }), [full, lang, overview]);
  return <TreeContextProvider tree={tree}><Sidebar {...props} /></TreeContextProvider>;
}

function EnabledChat({ lang, version, ...props }: DocsLayoutProps & { lang: Locale; version: string }) {
  const transport = useMemo(() => new DefaultChatTransport<Message>({
    api: '/api/chat',
    prepareSendMessagesRequest: ({ messages }) => ({
      body: {
        lang,
        version,
        messages: messages.map(({ role, parts }) => ({
          role,
          text: parts.filter((part) => part.type === 'text').map((part) => part.text).join('\n').slice(0, 1000),
          location: parts.find((part) => part.type === 'data-client')?.data,
        })).filter(({ text }) => text.trim()).slice(-8),
      },
    }),
  }), [lang, version]);
  const chat = useChat({ transport });

  return (
    <AIChatProvider chat={chat} description={false} suggestions={[]} renderPart={renderPart}
      toMessage={(text) => ({
        role: 'user',
        parts: [
          { type: 'text', text },
          { type: 'data-client', data: { location: window.location.pathname, title: document.title } },
        ],
      })}>
      <ChatLayout {...props} />
    </AIChatProvider>
  );
}

export function DocsShell({ lang, askAI, trees, children }: Pick<DocsLayoutProps, 'children'> &
  { lang: Locale; askAI: boolean; trees: Record<string, DocsLayoutProps['tree']> }) {
  const version = docsVersionAtPath(usePathname());
  const layoutProps: DocsLayoutProps = {
    children,
    tree: trees[version] ?? trees[latestDocsVersion],
    nav: { enabled: false, mode: 'top' },
    sidebar: { components: { Separator: DocsGroup } },
    slots: { sidebar: {
      provider: SidebarProvider, root: DocsSidebar, trigger: SidebarTrigger, collapseTrigger: SidebarCollapseTrigger, useSidebar,
    } },
    themeSwitch: { enabled: false },
    i18n: false,
    containerProps: { className: 'swb-container swb-docs' },
  };
  return askAI ? <EnabledChat key={version} lang={lang} version={version} {...layoutProps} /> : <DocsLayout {...layoutProps} />;
}
