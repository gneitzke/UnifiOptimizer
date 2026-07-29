import { useMemo, useState } from 'react';
import { TriangleAlert } from 'lucide-react';
import {
  Button,
  Card,
  DataTable,
  EmptyState,
  Skeleton,
  exactLocal,
  type Column,
} from '../../components/ui';
import { useHealth, type NetEvent } from '../../api';
import {
  FAMILIES,
  familyLabel,
  familyOf,
  eventSentence,
  isFaultKey,
  type FamilyId,
} from '../shared/eventVocab';
import { WINDOWS, bucketize, eventsInBucket } from './buckets';
import { EventDensityChart } from './EventDensityChart';
import { useTimelineEvents } from './useTimelineEvents';

/**
 * /timeline — network-wide event density. Bucket the window's events into a
 * hand-rolled density chart (volume in accent, faults in the one severity tint),
 * filter by key family, switch zoom windows, and click a bar to drill into that
 * slice's events. Honest empty/loading/error states throughout; the event table
 * keyboard-traverses via the shared DataTable.
 *
 * Every timestamp on this page goes through the shared `exactLocal` stamp
 * (24-hour, dated once it's not today) rather than a locale time string — the
 * 7D window can hold events from a different calendar day, and a bare
 * locale-formatted "04:27:00 PM" both leaked AM/PM and dropped the date
 * (Gitea #25).
 */

export default function TimelinePage() {
  const [windowIdx, setWindowIdx] = useState(2); // default 24H
  const [families, setFamilies] = useState<Set<FamilyId>>(() => new Set());
  const [selected, setSelected] = useState<number | null>(null);

  const spec = WINDOWS[windowIdx];
  const { events, nowTs, fetchedAt, loading, error, capped, reload } =
    useTimelineEvents(spec);
  const health = useHealth(60_000);

  const buckets = useMemo(
    () => bucketize(events, spec, nowTs, families),
    [events, spec, nowTs, families],
  );

  // Where does our data coverage actually begin? Anything earlier is un-observed,
  // not "quiet". Use the CONSERVATIVE (later) of monitoring-start and the oldest
  // event we hold, clamped by whether the fetch was capped:
  //  - not capped: coverage = min(monitoring-start, oldest event) — never hatches
  //    a covered-but-quiet leading span, but marks the pre-monitoring void.
  //  - capped: older events were truncated, so coverage begins at the oldest
  //    event we DID load; everything before it is un-fetched, not quiet.
  const { coverageStart, coverageLabel } = useMemo(() => {
    const oldestEventTs = events.length ? Math.min(...events.map((e) => e.ts)) : null;
    if (capped) {
      return oldestEventTs != null
        ? { coverageStart: oldestEventTs, coverageLabel: 'older events not loaded' }
        : { coverageStart: null, coverageLabel: undefined };
    }
    const monitoringSince =
      health.data && nowTs ? nowTs - health.data.uptime_s : null;
    const cands = [monitoringSince, oldestEventTs].filter(
      (v): v is number => v != null,
    );
    if (cands.length === 0) return { coverageStart: null, coverageLabel: undefined };
    const start = Math.min(...cands);
    const label =
      monitoringSince != null && start === monitoringSince
        ? `monitoring since ${exactLocal(start)}`
        : `data from ${exactLocal(start)}`;
    return { coverageStart: start, coverageLabel: label };
  }, [events, capped, health.data, nowTs]);

  const listEventsForView = useMemo((): NetEvent[] => {
    if (selected != null && buckets[selected]) {
      return eventsInBucket(events, buckets[selected], families);
    }
    // Default: window's events (family-filtered), newest first, capped for the table.
    const filterOn = families.size > 0;
    return events
      .filter((e) => e.ts >= nowTs - spec.seconds && e.ts < nowTs)
      .filter((e) => !filterOn || families.has(familyOf(e.key)))
      .slice(0, 300);
  }, [selected, buckets, events, families, nowTs, spec.seconds]);

  const selectedBucket = selected != null ? buckets[selected] : null;

  function toggleFamily(id: FamilyId) {
    setSelected(null);
    setFamilies((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const columns: Column<NetEvent>[] = [
    {
      key: 'ts',
      header: 'Time',
      width: 104,
      sortAccessor: (e) => e.ts,
      render: (e) => (
        <span
          className="tnum t-secondary whitespace-nowrap"
          style={{ color: 'var(--fg-muted)' }}
        >
          {exactLocal(e.ts)}
        </span>
      ),
    },
    {
      // Sentence primary, raw code secondary in mono (Gitea #25/#52: this used
      // to lead with the machine code — "WU Roam Radio" — and bury the human
      // sentence in a separate, truncated "Detail" column that duplicated Entity;
      // anomalies still surfaced as raw "ANOMALY_USER_HIGH_TCP_LATENCY"). The
      // plain sentence now comes from the shared vocabulary, and the exact key
      // stays beside it for the expert.
      key: 'event',
      header: 'Event',
      sortAccessor: (e) => eventSentence(e.key, e.msg),
      render: (e) => {
        const fault = isFaultKey(e.key);
        const sentence = eventSentence(e.key, e.msg);
        return (
          <span className="inline-flex items-baseline gap-1.5 min-w-0" title={sentence}>
            {fault ? (
              <TriangleAlert
                size={13}
                className="shrink-0 self-center"
                style={{ color: 'var(--sev-p2)' }}
                aria-label="fault"
              />
            ) : (
              <span
                aria-hidden
                className="inline-block rounded-full shrink-0"
                style={{ width: 6, height: 6, background: 'var(--fg-subtle)' }}
              />
            )}
            <span className="truncate" style={{ maxWidth: 420 }}>
              <span style={{ color: 'var(--fg)' }}>{sentence}</span>
              <code
                className="t-caption font-mono ml-2"
                style={{ color: 'var(--fg-subtle)' }}
              >
                {e.key}
              </code>
            </span>
          </span>
        );
      },
    },
    {
      key: 'family',
      header: 'Family',
      width: 120,
      sortAccessor: (e) => familyOf(e.key),
      render: (e) => (
        <span className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
          {familyLabel(familyOf(e.key))}
        </span>
      ),
    },
    {
      key: 'entity',
      header: 'Entity',
      width: 180,
      sortAccessor: (e) => e.entity?.name ?? '',
      render: (e) => {
        const name = e.entity?.name ?? e.related_entity?.name;
        return name ? (
          <span style={{ color: 'var(--fg)' }}>{name}</span>
        ) : (
          <span style={{ color: 'var(--fg-subtle)' }}>—</span>
        );
      },
    },
  ];

  const listTitle = selectedBucket
    ? `Events ${exactLocal(selectedBucket.t0)}–${exactLocal(selectedBucket.t1)}`
    : 'Recent events';

  return (
    <div className="px-6 sm:px-8 py-8 max-w-[1200px] mx-auto">
      <div className="flex items-start justify-between gap-4 mb-1">
        <h2 className="t-page-title" style={{ color: 'var(--fg)' }}>
          Timeline
        </h2>
      </div>
      <p className="t-secondary mb-5" style={{ color: 'var(--fg-muted)' }}>
        Network-wide event density. Filter by family, change the window, and click a
        bar to inspect its events.
      </p>

      {/* controls */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <div
          className="inline-flex rounded-control overflow-hidden"
          style={{ border: '1px solid var(--strong)' }}
          role="group"
          aria-label="Time window"
        >
          {WINDOWS.map((w, i) => {
            const on = i === windowIdx;
            return (
              <button
                key={w.id}
                type="button"
                onClick={() => {
                  setWindowIdx(i);
                  setSelected(null);
                }}
                aria-pressed={on}
                className="h-8 px-3 t-label transition-colors cursor-pointer"
                style={{
                  background: on ? 'var(--accent)' : 'transparent',
                  color: on ? 'var(--accent-fg)' : 'var(--fg-muted)',
                  borderLeft: i === 0 ? undefined : '1px solid var(--hairline)',
                }}
              >
                {w.label}
              </button>
            );
          })}
        </div>

        <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Family filter">
          {FAMILIES.map((f) => {
            const on = families.has(f.id);
            return (
              <button
                key={f.id}
                type="button"
                title={f.hint}
                onClick={() => toggleFamily(f.id)}
                aria-pressed={on}
                className="h-7 px-2.5 rounded-control t-caption transition-colors cursor-pointer"
                style={{
                  background: on ? 'var(--accent)' : 'var(--surface)',
                  color: on ? 'var(--accent-fg)' : 'var(--fg-muted)',
                  border: `1px solid ${on ? 'transparent' : 'var(--hairline)'}`,
                }}
              >
                {f.label}
              </button>
            );
          })}
          {families.size > 0 && (
            <Button variant="ghost" size="sm" onClick={() => setFamilies(new Set())}>
              Clear
            </Button>
          )}
        </div>

        <Button variant="ghost" size="sm" className="ml-auto" onClick={reload}>
          Refresh
        </Button>
      </div>

      {/* chart */}
      <Card className="mb-6">
        {loading && events.length === 0 ? (
          <div className="py-4">
            <Skeleton className="h-5 w-40 mb-4" />
            <Skeleton className="h-[200px] w-full" />
          </div>
        ) : error ? (
          <div className="py-10">
            <EmptyState
              variant="no-data"
              title="Couldn't load events"
              description={
                error.status === 0
                  ? 'The API is unreachable. Is the daemon running?'
                  : `The events endpoint returned ${error.status}.`
              }
              action={{ label: 'Retry', onClick: reload }}
            />
          </div>
        ) : events.length === 0 ? (
          <div className="py-10">
            <EmptyState
              variant="no-data"
              title="No events in this window"
              description="Nothing has been recorded for the selected period yet."
            />
          </div>
        ) : (
          <EventDensityChart
            buckets={buckets}
            selected={selected}
            onSelect={setSelected}
            asOf={exactLocal(fetchedAt)}
            coverageStart={coverageStart}
            coverageLabel={coverageLabel}
          />
        )}
        {capped && !loading && !error && (
          <div className="t-caption mt-3" style={{ color: 'var(--fg-subtle)' }}>
            Showing the most recent {events.length} events; the window may hold more.
          </div>
        )}
      </Card>

      {/* event list */}
      <div className="flex items-baseline justify-between gap-3 mb-2">
        <div className="t-section" style={{ color: 'var(--fg)' }}>
          {listTitle}
          <span className="t-secondary tnum ml-2" style={{ color: 'var(--fg-subtle)' }}>
            {listEventsForView.length}
          </span>
        </div>
        {selected != null && (
          <Button variant="ghost" size="sm" onClick={() => setSelected(null)}>
            Clear selection
          </Button>
        )}
      </div>
      <Card pad="none">
        {!loading && listEventsForView.length === 0 ? (
          <EmptyState
            variant={families.size > 0 || selected != null ? 'no-match' : 'no-data'}
            title={selected != null ? 'No events in this slice' : 'No matching events'}
            description={
              families.size > 0
                ? 'No events match the current family filter.'
                : 'This time slice recorded no events.'
            }
            action={
              families.size > 0
                ? { label: 'Clear filters', onClick: () => setFamilies(new Set()) }
                : selected != null
                  ? { label: 'Clear selection', onClick: () => setSelected(null) }
                  : undefined
            }
          />
        ) : (
          <div className="px-2">
            <DataTable
              columns={columns}
              rows={listEventsForView}
              rowKey={(e) => e.id}
              initialSort={{ key: 'ts', dir: 'desc' }}
            />
          </div>
        )}
      </Card>
    </div>
  );
}
