'use client';

import { buttonVariants } from 'fumadocs-ui/components/ui/button';
import { SidebarCollapseTrigger, SidebarTrigger } from 'fumadocs-ui/layouts/notebook/slots/sidebar';
import { PanelLeft, PanelRight } from 'lucide-react';

export function DocsNavigation() {
  const style = buttonVariants({ variant: 'secondary', size: 'icon-sm' });
  return (
    <>
      <SidebarTrigger className={`ml-auto ${style} md:hidden`}><PanelRight aria-hidden="true" /></SidebarTrigger>
      <SidebarCollapseTrigger className={`ml-auto ${style} hidden md:inline-flex`}><PanelLeft aria-hidden="true" /></SidebarCollapseTrigger>
    </>
  );
}
