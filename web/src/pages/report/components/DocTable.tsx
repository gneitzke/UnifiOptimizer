import type { ReactNode } from 'react';
import { cn } from '../../../components/ui/cn';

/**
 * A plain, print-friendly document table — no sort controls or row navigation
 * (this is a report, not a dashboard). Numeric columns are right-aligned tabular
 * figures with units in the header (docs/DESIGN_FOUNDATION.md §Typography, rule 7).
 * Header rows repeat across print page breaks via `thead` (see print.css).
 */

export interface DocColumn<T> {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  numeric?: boolean;
  align?: 'left' | 'right';
  width?: string | number;
}

export function DocTable<T>({
  columns,
  rows,
  rowKey,
  className,
}: {
  columns: DocColumn<T>[];
  rows: T[];
  rowKey: (row: T, i: number) => string | number;
  className?: string;
}) {
  return (
    <div className={cn('w-full overflow-x-auto', className)}>
      <table className="w-full text-[13px]" style={{ borderCollapse: 'collapse' }}>
        <thead>
          <tr style={{ borderBottom: '1px solid var(--hairline)' }}>
            {columns.map((c) => {
              const right = c.align === 'right' || (c.numeric && c.align !== 'left');
              return (
                <th
                  key={c.key}
                  className="t-label font-medium py-2 px-3"
                  style={{
                    color: 'var(--fg-muted)',
                    textAlign: right ? 'right' : 'left',
                    width: c.width,
                  }}
                >
                  {c.header}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={rowKey(row, i)} style={{ borderBottom: '1px solid var(--hairline)' }}>
              {columns.map((c) => {
                const right = c.align === 'right' || (c.numeric && c.align !== 'left');
                return (
                  <td
                    key={c.key}
                    className={cn('py-2 px-3', c.numeric && 'tnum')}
                    style={{ color: 'var(--fg)', textAlign: right ? 'right' : 'left' }}
                  >
                    {c.render(row)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
