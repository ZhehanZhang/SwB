'use client';

import { AIChatTrigger } from '@fumadocs/ai-chat';
import { SidebarCollapseTrigger, SidebarTrigger } from 'fumadocs-ui/layouts/notebook/slots/sidebar';
import { PanelLeft, PanelRight } from 'lucide-react';
import styles from './page-actions.module.css';

export function DocsNavigation({ askAI }: { askAI: boolean }) {
  const icon = `${styles.button} ${styles.icon}`;
  return (
    <div className={styles.navigation}>
      <SidebarTrigger className={`${icon} ${styles.mobileSidebar}`}><PanelRight aria-hidden="true" /></SidebarTrigger>
      <SidebarCollapseTrigger className={`${icon} ${styles.desktopSidebar}`}><PanelLeft aria-hidden="true" /></SidebarCollapseTrigger>
      {askAI && <AIChatTrigger className={`${icon} ${styles.ask}`} />}
    </div>
  );
}
