import { Children, isValidElement, type ReactNode } from 'react';

export function codeText(node: ReactNode): string {
  let text = '';
  Children.forEach(node, (child) => {
    if (typeof child === 'string') text += child;
    else if (isValidElement<{ children?: ReactNode }>(child)) text += codeText(child.props.children);
  });
  return text;
}
