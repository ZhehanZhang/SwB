'use client';

import { AIChatTrigger } from '@fumadocs/ai-chat';
import { SidebarCollapseTrigger, SidebarTrigger } from 'fumadocs-ui/layouts/notebook/slots/sidebar';
import { PanelLeft, PanelRight } from 'lucide-react';

export function DocsNavigation({ askAI }: { askAI: boolean }) {
  const style = 'swb-button swb-button-icon swb-button-outline';
  return (
    <div className="ml-auto flex items-center gap-2">
      <SidebarTrigger className={`${style} md:hidden`}><PanelRight aria-hidden="true" /></SidebarTrigger>
      {askAI && <AIChatTrigger className={`${style} swb-ask-mobile md:hidden`} />}
      <SidebarCollapseTrigger className={`${style} hidden md:inline-flex`}><PanelLeft aria-hidden="true" /></SidebarCollapseTrigger>
    </div>
  );
}
