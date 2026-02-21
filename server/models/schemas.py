"""Pydantic models for API request/response schemas."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class ManualDeviceRequest(BaseModel):
    host: str = Field(..., description="Controller URL (e.g., https://192.168.1.1)")
    device_type: str = Field(
        "auto", description="cloudkey_gen2 | dream_machine | dream_machine_pro | self_hosted | auto"
    )


class LoginRequest(BaseModel):
    host: str
    username: str
    password: str
    site: str = "default"
    remember: bool = True


class LoginResponse(BaseModel):
    token: str
    expires_in: int = Field(description="Seconds until token expires")
    controller_type: str
    site: str


class AuthStatus(BaseModel):
    authenticated: bool
    host: Optional[str] = None
    username: Optional[str] = None
    site: Optional[str] = None
    expires_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class DiscoveredDevice(BaseModel):
    host: str
    port: int
    device_type: str
    name: Optional[str] = None
    model: Optional[str] = None


class DiscoverResponse(BaseModel):
    devices: List[DiscoveredDevice]
    scan_duration_ms: int


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


class AnalysisRequest(BaseModel):
    lookback_days: int = 3
    min_rssi_strategy: str = "optimal"


class AnalysisJob(BaseModel):
    job_id: str
    status: str = Field(description="pending | running | completed | failed")
    progress: int = Field(0, description="0-100 percentage")
    message: Optional[str] = None


class AnalysisResult(BaseModel):
    job_id: str
    health_score: Optional[Dict[str, Any]] = None
    ap_analysis: Optional[Dict[str, Any]] = None
    client_analysis: Optional[Dict[str, Any]] = None
    recommendations: List[Dict[str, Any]] = []
    full_analysis: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Repair
# ---------------------------------------------------------------------------


class RepairPreviewRequest(BaseModel):
    recommendation_ids: List[int] = Field(
        ..., description="Indices of recommendations to preview"
    )


class ChangePreview(BaseModel):
    index: int
    device_name: str
    action: str
    current_value: str
    new_value: str
    impact: Dict[str, Any]


class RepairApplyRequest(BaseModel):
    recommendation_ids: List[int]
    dry_run: bool = False


class ChangeResult(BaseModel):
    change_id: str
    device_name: str
    action: str
    status: str = Field(description="applied | failed | skipped | dry_run")
    before_config: Optional[Dict[str, Any]] = None
    after_config: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    timestamp: str


class RevertRequest(BaseModel):
    change_id: str


class ChangeHistoryEntry(BaseModel):
    change_id: str
    device_name: str
    device_mac: str
    action: str
    before_config: Dict[str, Any]
    after_config: Dict[str, Any]
    status: str
    timestamp: str
    reverted: bool = False
    reverted_at: Optional[str] = None
