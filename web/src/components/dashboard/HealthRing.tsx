import { useEffect, useState } from 'react';

function getColor(score: number): string {
  if (score > 90) return 'var(--success)';
  if (score > 75) return 'var(--primary)';
  if (score > 60) return 'var(--warning)';
  if (score > 40) return '#ff8c00';
  return 'var(--error)';
}

function getGrade(score: number): string {
  if (score >= 93) return 'A';
  if (score >= 85) return 'B+';
  if (score >= 75) return 'B';
  if (score >= 65) return 'C+';
  if (score >= 55) return 'C';
  if (score >= 40) return 'D';
  return 'F';
}

interface HealthRingProps {
  score: number;
  size?: number;
  strokeWidth?: number;
}

export default function HealthRing({
  score,
  size = 160,
  strokeWidth = 8,
}: HealthRingProps) {
  const [mounted, setMounted] = useState(false);
  const r = (size - strokeWidth) / 2 - 4;
  const circ = 2 * Math.PI * r;
  const offset = mounted
    ? circ * (1 - score / 100)
    : circ;
  const color = getColor(score);
  const half = size / 2;

  useEffect(() => {
    const id = requestAnimationFrame(
      () => setMounted(true),
    );
    return () => cancelAnimationFrame(id);
  }, []);

  return (
    <div
      className="relative mx-auto"
      style={{ width: size, height: size }}
    >
      <svg
        viewBox={`0 0 ${size} ${size}`}
        className="w-full h-full"
      >
        <circle
          cx={half} cy={half} r={r}
          fill="none"
          stroke="var(--border-strong)"
          strokeWidth={strokeWidth}
        />
        <circle
          cx={half} cy={half} r={r}
          fill="none"
          stroke={color}
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeDasharray={circ}
          strokeDashoffset={offset}
          transform={
            `rotate(-90 ${half} ${half})`
          }
          style={{
            transition:
              'stroke-dashoffset 1.2s ease',
          }}
        />
      </svg>
      <div
        className="absolute inset-0 flex flex-col
          items-center justify-center"
      >
        <span
          className="text-3xl font-bold"
          style={{ color }}
        >
          {score}
        </span>
        <span
          className="text-xs font-semibold"
          style={{ color: 'var(--text-muted)' }}
        >
          {getGrade(score)}
        </span>
      </div>
    </div>
  );
}
