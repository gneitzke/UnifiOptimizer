import type { ElementType } from 'react';
import {
  TrendingUp,
  TrendingDown,
  Minus,
} from 'lucide-react';

interface StatCardProps {
  title: string;
  value: string | number;
  subtitle?: string;
  icon: ElementType;
  trend?: 'up' | 'down' | 'flat';
  iconColor?: string;
}

const TREND_ICON = {
  up: TrendingUp,
  down: TrendingDown,
  flat: Minus,
} as const;

const TREND_COLOR = {
  up: 'var(--success)',
  down: 'var(--error)',
  flat: 'var(--text-muted)',
} as const;

export default function StatCard({
  title,
  value,
  subtitle,
  icon: Icon,
  trend,
  iconColor = 'var(--primary)',
}: StatCardProps) {
  const TrendIcon = trend
    ? TREND_ICON[trend]
    : null;

  return (
    <div
      className="glass-card p-5 flex
        items-center gap-4"
      style={{
        backdropFilter: 'blur(12px)',
      }}
    >
      <div
        className="w-10 h-10 rounded-xl flex
          items-center justify-center shrink-0"
        style={{
          background: `color-mix(
            in srgb, ${iconColor} 15%, transparent
          )`,
        }}
      >
        <Icon
          size={20}
          style={{ color: iconColor }}
        />
      </div>
      <div className="flex-1 min-w-0">
        <p
          className="text-xs"
          style={{
            color: 'var(--text-muted)',
          }}
        >
          {title}
        </p>
        <p
          className="text-lg font-semibold"
          style={{ color: 'var(--text)' }}
        >
          {value}
        </p>
        {subtitle && (
          <p
            className="text-[11px] truncate"
            style={{
              color: 'var(--text-muted)',
            }}
          >
            {subtitle}
          </p>
        )}
      </div>
      {TrendIcon && (
        <TrendIcon
          size={16}
          style={{
            color: TREND_COLOR[trend!],
          }}
        />
      )}
    </div>
  );
}
