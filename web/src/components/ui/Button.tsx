import type { ButtonHTMLAttributes } from 'react';
import { cn } from './cn';

/**
 * Restrained button. One accent (never-do rule 1): the primary variant is the
 * only place solid accent fill appears in a control. Radius/shadow are modest
 * and vary by variant, not applied reflexively.
 */

type Variant = 'primary' | 'secondary' | 'ghost';
type Size = 'sm' | 'md';

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

const VARIANT: Record<Variant, string> = {
  primary:
    'bg-accent text-accent-fg border border-transparent hover:opacity-90',
  secondary:
    'bg-surface text-fg border border-strong hover:bg-canvas',
  ghost:
    'bg-transparent text-fg-muted border border-transparent hover:text-fg hover:bg-canvas',
};

const SIZE: Record<Size, string> = {
  sm: 'h-8 px-3 text-[13px]',
  md: 'h-9 px-4 text-[14px]',
};

export function Button({
  variant = 'secondary',
  size = 'md',
  className,
  type = 'button',
  ...rest
}: Props) {
  return (
    <button
      type={type}
      className={cn(
        'inline-flex items-center justify-center gap-1.5 rounded-control',
        'font-medium transition-colors cursor-pointer select-none',
        'disabled:opacity-40 disabled:cursor-not-allowed',
        VARIANT[variant],
        SIZE[size],
        className,
      )}
      {...rest}
    />
  );
}
