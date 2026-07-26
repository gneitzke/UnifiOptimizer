import type { HTMLAttributes } from 'react';
import { cn } from './cn';

/**
 * Surface container. No glassmorphism, no glow (never-do rule 10). Elevation is
 * lighter surface + hairline border + stronger shadow (docs §Color tokens),
 * driven by tokens so both themes re-pick correctly.
 */

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  elevated?: boolean;
  /** Card padding preset (docs §Spacing: card padding 16-20). */
  pad?: 'none' | 'sm' | 'md';
}

export function Card({
  elevated = false,
  pad = 'md',
  className,
  style,
  ...rest
}: CardProps) {
  const padCls = pad === 'none' ? '' : pad === 'sm' ? 'p-3' : 'p-4';
  return (
    <div
      className={cn(
        'bg-surface border border-hairline rounded-card',
        padCls,
        className,
      )}
      style={{
        boxShadow: elevated
          ? 'var(--shadow-elevated)'
          : 'var(--shadow-card)',
        background: elevated ? 'var(--elevated)' : undefined,
        ...style,
      }}
      {...rest}
    />
  );
}
