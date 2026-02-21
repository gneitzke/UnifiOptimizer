import Dexie, { type EntityTable } from 'dexie';

/* ── Schema ────────────────────────────────────── */

export interface AnalysisRecord {
  id?: number;
  jobId: string;
  timestamp: string;
  healthScore: number;
  apCount: number;
  clientCount: number;
  summary: string;
}

/* ── Database ──────────────────────────────────── */

const db = new Dexie('unifi-optimizer') as Dexie & {
  analysisHistory: EntityTable<AnalysisRecord, 'id'>;
};

db.version(1).stores({
  analysisHistory:
    '++id, jobId, timestamp, healthScore',
});

/* ── CRUD helpers ──────────────────────────────── */

export async function saveAnalysis(
  record: Omit<AnalysisRecord, 'id'>,
): Promise<number> {
  const id = await db.analysisHistory.add(
    record as AnalysisRecord,
  );
  return id as number;
}

export async function getHistory(): Promise<
  AnalysisRecord[]
> {
  return db.analysisHistory
    .orderBy('timestamp')
    .reverse()
    .toArray();
}

export async function getAnalysis(
  id: number,
): Promise<AnalysisRecord | undefined> {
  return db.analysisHistory.get(id);
}

export async function deleteAnalysis(
  id: number,
): Promise<void> {
  return db.analysisHistory.delete(id);
}

export { db };
