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
  name?: string;
  model?: string;
  version?: string;
  mac?: string;
  hostname?: string;
  informUrl?: string;
}

export interface HealthScore {
  overall: number;
  grade?: string;
  status?: string;
  wireless: number;
  wirelessMax: number;
  wired: number;
  wiredMax: number;
  latency: number;
  latencyMax: number;
  coverage: number;
  coverageMax: number;
  issuesScore?: number;
}

export interface RadioInfo {
  band: string;
  channel: number;
  width: number;
  txPower: number;
  txPowerMode: string;
}

export interface ApAnalysis {
  mac: string;
  name: string;
  model: string;
  isMesh: boolean;
  channel: number;
  band: string;
  txPower: number;
  clients: number;
  satisfaction: number;
  interference: number;
  radios: RadioInfo[];
  issues: string[];
  suggestions: string[];
}

export interface SignalDistribution {
  excellent: number;
  good: number;
  fair: number;
  poor: number;
  critical: number;
  wired: number;
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
  message: string;
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
  signalDistribution: SignalDistribution;
  channelUsage: Record<string, string[]>;
  topology: TopologyNode[];
  apCount: number;
  clientCount: number;
  summary: string;
  findings: Finding[];
  timeline: EventTimeline;
  componentScores: ComponentScores;
  clientCapabilities: ClientCapabilities;
  manufacturers: ManufacturerStats[];
  clientJourneys: ClientJourney[];
}

export interface TopologyNode {
  name: string;
  mac: string;
  type: 'switch' | 'ap' | 'mesh';
  parentName: string;
  clients: number;
  model: string;
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
  realChangeId?: string;
  success: boolean;
  appliedAt: string;
  revertible: boolean;
  error?: string;
}

export interface EventTimeline {
  satisfactionByHour: Record<string, number>;
  categories: Record<string, number[]>;
  hours: string[];
  apEvents: Record<string, Record<string, number>>;
  totals: Record<string, number>;
  lookbackDays: number;
}

export interface ComponentScores {
  rfHealth: number;
  clientHealth: number;
  infrastructure: number;
  security: number;
}

export interface ClientCapabilities {
  wifiStandards: Record<string, number>;
  channelWidths: Record<string, number>;
  spatialStreams: Record<string, number>;
}

export interface ManufacturerStats {
  name: string;
  count: number;
  type: string;
  icon: string;
}

export interface ClientJourney {
  hostname: string;
  mac: string;
  currentRssi: number;
  currentAp: string;
  behavior: string;
  behaviorDetail: string;
  disconnectCount: number;
  roamCount: number;
  visitedAps: string[];
  apPath: { ts: number; fromAp: string; toAp: string; channelFrom: string; channelTo: string }[];
}

export interface ChangeHistoryEntry {
  changeId: string;
  change_id?: string;
  description: string;
  deviceName: string;
  device_name?: string;
  setting: string;
  previousValue: string;
  newValue: string;
  appliedAt: string;
  appliedBy: string;
  reverted: boolean;
  revertedAt?: string;
  action?: string;
  status?: string;
  timestamp?: string;
  device_mac?: string;
}
