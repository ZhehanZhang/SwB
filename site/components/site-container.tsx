import type { ComponentPropsWithoutRef } from 'react';

export function SiteContainer({ as: Tag = 'div', className = '', ...props }: ComponentPropsWithoutRef<'div'> & { as?: 'div' | 'nav' }) {
  return <Tag className={`swb-container ${className}`} {...props} />;
}
