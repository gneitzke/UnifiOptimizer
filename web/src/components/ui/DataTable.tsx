import { useMemo, useState, type ReactNode } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { useListNavigation } from '../../layout/keyboard/useListNavigation';
import { cn } from './cn';

/**
 * Dense, sortable table (docs §Spacing, §Charts-adjacent numeric rules):
 * - numeric cells are RIGHT-aligned tabular-nums, never proportional/centered
 *   figures (never-do rule 7); units live in the header, not the cells;
 * - 40px rows by default (44 for the issues list — a primary click target);
 * - whole-row activation, with j/k + arrow traversal and Enter to open
 *   (docs §Interaction), via the shared useListNavigation primitive.
 */

export interface Column<T> {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  /** Enables sorting on this column. */
  sortAccessor?: (row: T) => number | string | null;
  numeric?: boolean;
  align?: 'left' | 'right' | 'center';
  width?: number | string;
}

interface Props<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string | number;
  onRowActivate?: (row: T, index: number) => void;
  rowHeight?: number;
  initialSort?: { key: string; dir: 'asc' | 'desc' };
  empty?: ReactNode;
  className?: string;
}

type Dir = 'asc' | 'desc';

function alignClass<T>(col: Column<T>): string {
  const a = col.align ?? (col.numeric ? 'right' : 'left');
  return a === 'right' ? 'text-right' : a === 'center' ? 'text-center' : 'text-left';
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  onRowActivate,
  rowHeight = 40,
  initialSort,
  empty,
  className,
}: Props<T>) {
  const [sort, setSort] = useState<{ key: string; dir: Dir } | null>(
    initialSort ?? null,
  );

  const sorted = useMemo(() => {
    if (!sort) return rows;
    const col = columns.find((c) => c.key === sort.key);
    if (!col?.sortAccessor) return rows;
    const acc = col.sortAccessor;
    const factor = sort.dir === 'asc' ? 1 : -1;
    return [...rows].sort((a, b) => {
      const va = acc(a);
      const vb = acc(b);
      if (va == null && vb == null) return 0;
      if (va == null) return 1; // nulls sink regardless of direction
      if (vb == null) return -1;
      if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * factor;
      return String(va).localeCompare(String(vb)) * factor;
    });
  }, [rows, sort, columns]);

  const nav = useListNavigation(sorted.length, (i) => {
    const row = sorted[i];
    if (row !== undefined) onRowActivate?.(row, i);
  });

  function toggleSort(col: Column<T>) {
    if (!col.sortAccessor) return;
    setSort((prev) => {
      if (prev?.key !== col.key) return { key: col.key, dir: 'asc' };
      return { key: col.key, dir: prev.dir === 'asc' ? 'desc' : 'asc' };
    });
  }

  if (rows.length === 0 && empty) {
    return <div className={className}>{empty}</div>;
  }

  return (
    <div
      className={cn('w-full overflow-x-auto outline-none', className)}
      tabIndex={nav.containerProps.tabIndex}
      onKeyDown={nav.containerProps.onKeyDown}
    >
      <table role="grid" className="w-full border-collapse text-[14px]">
        <thead>
          <tr role="row" style={{ borderBottom: '1px solid var(--hairline)' }}>
            {columns.map((col) => {
              const active = sort?.key === col.key;
              const rightAligned =
                (col.align ?? (col.numeric ? 'right' : 'left')) === 'right';
              const inner = (
                <>
                  {col.header}
                  {active &&
                    (sort?.dir === 'asc' ? (
                      <ChevronUp size={13} />
                    ) : (
                      <ChevronDown size={13} />
                    ))}
                </>
              );
              return (
                <th
                  key={col.key}
                  scope="col"
                  className={cn('px-3 h-9 t-label font-medium select-none', alignClass(col))}
                  style={{ color: 'var(--fg-muted)', width: col.width }}
                  aria-sort={
                    active ? (sort?.dir === 'asc' ? 'ascending' : 'descending') : undefined
                  }
                >
                  {col.sortAccessor ? (
                    // Real button so keyboard/AT users can sort (Enter/Space),
                    // not a mouse-only <th onClick>. Inherits the header type.
                    <button
                      type="button"
                      onClick={() => toggleSort(col)}
                      className={cn(
                        'inline-flex items-center gap-1 cursor-pointer select-none bg-transparent',
                        rightAligned && 'flex-row-reverse',
                      )}
                      style={{ color: 'inherit', font: 'inherit', letterSpacing: 'inherit' }}
                    >
                      {inner}
                    </button>
                  ) : (
                    <span
                      className={cn('inline-flex items-center gap-1', rightAligned && 'flex-row-reverse')}
                    >
                      {inner}
                    </span>
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row, i) => {
            const rp = nav.getRowProps(i);
            const isActive = i === nav.activeIndex;
            return (
              <tr
                key={rowKey(row)}
                role="row"
                ref={rp.ref as (el: HTMLTableRowElement | null) => void}
                aria-selected={rp['aria-selected']}
                onMouseEnter={rp.onMouseEnter}
                onClick={() => onRowActivate?.(row, i)}
                className={cn('transition-colors', onRowActivate && 'cursor-pointer')}
                style={{
                  height: rowHeight,
                  borderBottom: '1px solid var(--hairline)',
                  background: isActive ? 'var(--canvas)' : undefined,
                }}
              >
                {columns.map((col) => (
                  <td
                    key={col.key}
                    role="gridcell"
                    className={cn(
                      'px-3 t-body',
                      alignClass(col),
                      col.numeric && 'tnum',
                    )}
                    style={{ color: 'var(--fg)' }}
                  >
                    {col.render(row)}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
