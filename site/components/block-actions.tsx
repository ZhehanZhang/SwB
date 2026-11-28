import { BookOpen } from 'lucide-react';
import Link from 'next/link';
import type { ReactNode } from 'react';

export function BlockHeader({ children, guides }: { children: ReactNode; guides: ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-3" data-block-header>
      {children}
      <div className="swb-action-row" data-block-guides>{guides}</div>
    </div>
  );
}

export function GuideLink({ href, children }: { href: string; children: string }) {
  return (
    <Link className="swb-text-link inline-flex items-baseline gap-1.5 text-sm text-fd-muted-foreground" href={href}>
      <BookOpen aria-hidden="true" className="relative top-[.125em] size-4 shrink-0" />
      <span>{children}</span>
    </Link>
  );
}
