'use client';

import { useChat } from '@ai-sdk/react';
import { AIChatPanel, AIChatProvider, AIChatSearch, AIChatTrigger, useAIChat, type AIChatClientData } from '@fumadocs/ai-chat';
import { DefaultChatTransport, type UIMessage } from 'ai';
import { usePathname } from 'fumadocs-core/framework';
import { DocsLayout, type DocsLayoutProps } from 'fumadocs-ui/layouts/notebook';
import { useMemo } from 'react';
import { docsVersionAtPath, latestDocsVersion } from '@/lib/docs-version';
import type { Locale } from '@/lib/i18n';

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
      <AIChatTrigger className="swb-button swb-ask-trigger hidden md:inline-flex" />
    </DocsLayout>
  );
}

function renderPart(part: Message['parts'][number], live: boolean) {
  return part.type === 'tool-search' ? <AIChatSearch part={part} live={live} /> : null;
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
    themeSwitch: { enabled: false },
    i18n: false,
    containerProps: { className: `swb-container swb-docs${askAI ? ' swb-docs-with-chat' : ''}` },
  };
  return askAI ? <EnabledChat key={version} lang={lang} version={version} {...layoutProps} /> : <DocsLayout {...layoutProps} />;
}
