/* Ubiquiti Optimizer – API type definitions */

export interface LoginRequest {
  host: string;
  username: string;
  password: string;
  site?: string;
  remember?: boolean;
}

export interface LoginResponse {
  token: string;
  host: string;
  site: string;
  username: string;
  expiresAt: string;
}

export interface AuthStatus {
  authenticated: boolean;
  host?: string;
  username?: string;
  site?: string;
}

export interface DiscoveredDevice {
  host: string;
  port?: number;
  device_type?: string;
  name: string;
  model: string;
  version: string;
  mac: string;
  hostname?: string;
  informUrl?: string;
}

export interface HealthScore {
  overall: number;
  wireless: number;
  wired: number;
  latency: number;
  coverage: number;
}

export interface ApAnalysis {
  mac: string;
  name: string;
  model: string;
  channel: number;
  band: string;
  txPower: number;
  clients: number;
  satisfaction: number;
  interference: number;
  issues: string[];
  suggestions: string[];
}

export interface ClientAnalysis {
  mac: string;
  hostname: string;
  apMac: string;
  signal: number;
  noise: number;
  txRate: number;
  rxRate: number;
  satisfaction: number;
  issues: string[];
}

export type JobStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed';

export interface AnalysisJob {
  jobId: string;
  status: JobStatus;
  progress: number;
  startedAt: string;
  completedAt?: string;
  error?: string;
}

export interface AnalysisResult {
  jobId: string;
  timestamp: string;
  health: HealthScore;
  aps: ApAnalysis[];
  clients: ClientAnalysis[];
  apCount: number;
  clientCount: number;
  summary: string;
  findings: Finding[];
}

export interface Finding {
  id: string;
  severity: 'critical' | 'warning' | 'info';
  category: string;
  title: string;
  description: string;
  affectedDevices: string[];
}

export interface ChangePreview {
  changeId: string;
  description: string;
  deviceMac: string;
  deviceName: string;
  setting: string;
  currentValue: string;
  proposedValue: string;
  risk: 'low' | 'medium' | 'high';
}

export interface ChangeResult {
  changeId: string;
  success: boolean;
  appliedAt: string;
  revertible: boolean;
  error?: string;
}

export interface ChangeHistoryEntry {
  changeId: string;
  description: string;
  deviceName: string;
  setting: string;
  previousValue: string;
  newValue: string;
  appliedAt: string;
  appliedBy: string;
  reverted: boolean;
  revertedAt?: string;
}
