import { cn } from './cn';

/**
 * Neutral loading placeholder. Plain rectangles only — never a fake chart shape
 * or placeholder number (never-do rule 8). Use only when a load exceeds ~500ms
 * (docs §Interaction); below that, show nothing.
 */

interface Props {
  className?: string;
  width?: number | string;
  height?: number | string;
  rounded?: boolean;
}

export function Skeleton({ className, width, height, rounded }: Props) {
  return (
    <span
      aria-hidden
      className={cn(
        'block animate-pulse bg-hairline',
        rounded ? 'rounded-full' : 'rounded-control',
        className,
      )}
      style={{ width, height: height ?? '1em' }}
    />
  );
}
