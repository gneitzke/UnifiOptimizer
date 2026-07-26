/**
 * Data layer for the report surface (`/report`, docs/ARCHITECTURE.md §19).
 *
 * One read: `GET /api/report` returns the whole {@link ReportModel}, assembled
 * server-side from real repository queries. The read is open on the LAN (§18.1),
 * so no token is attached and no auth prompt is raised — this is a viewing
 * surface. The page renders what this returns and never computes a number itself.
 */

import { authHeaders } from '../../api/token';
import { fromWire } from './fromWire';
import type { ReportModel } from './model';
import type { WireReport } from './wire';

const BASE = import.meta.env.VITE_API_URL ?? '';

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export async function getReport(): Promise<ReportModel> {
  let res: Response;
  try {
    res = await fetch(`${BASE}/api/report`, {
      headers: { Accept: 'application/json', ...authHeaders() },
    });
  } catch (cause) {
    // Network failure / daemon down — a first-class state, not a thrown 500.
    throw new ApiError(0, `network error: ${(cause as Error).message}`);
  }
  if (!res.ok) {
    throw new ApiError(res.status, `${res.status} ${res.statusText}`.trim());
  }
  // The backend returns the wire shape (netadmin/report/models.py); adapt it to
  // the view model the sections render. The adapter only renames/restructures —
  // it computes no number of its own (see fromWire.ts).
  return fromWire((await res.json()) as WireReport);
}
