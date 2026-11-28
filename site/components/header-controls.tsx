'use client';

import { PopoverContent, PopoverTrigger } from 'fumadocs-ui/components/ui/popover';
import type { ComponentProps } from 'react';

export const headerIconButton = 'grid size-9 shrink-0 place-items-center rounded-md p-1.5 text-fd-muted-foreground transition-colors duration-100 hover:bg-fd-muted hover:text-fd-foreground data-[state=open]:bg-fd-accent';

export function HeaderControl({ className = '', ...props }: ComponentProps<typeof PopoverTrigger>) {
  return <PopoverTrigger type="button" className={`${headerIconButton} ${className}`} {...props} />;
}

export function HeaderPopover({ className = '', ...props }: ComponentProps<typeof PopoverContent>) {
  return (
    <PopoverContent align="end" collisionPadding={20}
      className={`max-w-[calc(100vw-2.5rem)] bg-fd-popover motion-reduce:data-[state=closed]:animate-none motion-reduce:data-[state=open]:animate-none ${className}`} {...props} />
  );
}
