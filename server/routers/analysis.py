"""Analysis router — run, poll, and retrieve network analyses."""

import os
import sys
import threading
import uuid
from typing import Dict

from fastapi import APIRouter, Depends, Header, HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from server.models.schemas import AnalysisJob, AnalysisRequest, AnalysisResult
from server.services.session_manager import session_pool

router = APIRouter()

# In-memory job store  (production would use Redis or similar)
_jobs: Dict[str, dict] = {}
_results: Dict[str, dict] = {}
_lock = threading.Lock()


def _get_token(authorization: str = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    return authorization[7:]


# ---------------------------------------------------------------------------
# Background analysis worker
# ---------------------------------------------------------------------------


def _run_analysis_worker(job_id: str, token: str, req: AnalysisRequest):
    """Background thread that runs the full analysis pipeline."""
    from core.services.analysis_service import run_analysis
    from core.services.recommendation_service import convert_recommendations

    entry = session_pool.get_session(token)
    if entry is None:
        with _lock:
            _jobs[job_id] = {"status": "failed", "progress": 0, "message": "Session expired"}
        return

    with _lock:
        _jobs[job_id] = {"status": "running", "progress": 10, "message": "Collecting data..."}

    try:
        result = run_analysis(
            entry.client,
            site=entry.site,
            lookback_days=req.lookback_days,
            min_rssi_strategy=req.min_rssi_strategy,
        )

        with _lock:
            _jobs[job_id] = {"status": "running", "progress": 80, "message": "Converting recs..."}

        analysis = result["full_analysis"]
        converted, skipped = convert_recommendations(
            analysis.get("recommendations", []),
            result["devices"],
        )

        with _lock:
            _results[job_id] = {
                "full_analysis": analysis,
                "recommendations": converted,
                "skipped_recommendations": skipped,
                "devices": result["devices"],
            }
            _jobs[job_id] = {"status": "completed", "progress": 100, "message": "Done"}

    except Exception as e:
        with _lock:
            _jobs[job_id] = {"status": "failed", "progress": 0, "message": str(e)}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/run", response_model=AnalysisJob)
async def run_analysis_endpoint(req: AnalysisRequest, authorization: str = Header(None)):
    """Start a new analysis job (returns immediately with a job_id)."""
    token = _get_token(authorization)
    entry = session_pool.get_session(token)
    if entry is None:
        raise HTTPException(status_code=401, detail="Session expired")

    job_id = str(uuid.uuid4())[:8]
    with _lock:
        _jobs[job_id] = {"status": "pending", "progress": 0, "message": "Queued"}

    thread = threading.Thread(target=_run_analysis_worker, args=(job_id, token, req), daemon=True)
    thread.start()

    return AnalysisJob(job_id=job_id, status="pending", progress=0, message="Queued")


@router.get("/status/{job_id}", response_model=AnalysisJob)
async def analysis_status(job_id: str):
    """Poll the status of a running analysis job."""
    with _lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return AnalysisJob(job_id=job_id, **job)


@router.get("/results/{job_id}", response_model=AnalysisResult)
async def analysis_results(job_id: str):
    """Retrieve completed analysis results."""
    with _lock:
        job = _jobs.get(job_id)
        result = _results.get(job_id)

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "completed":
        raise HTTPException(status_code=409, detail=f"Job status: {job['status']}")
    if result is None:
        raise HTTPException(status_code=500, detail="Results missing")

    analysis = result["full_analysis"]
    return AnalysisResult(
        job_id=job_id,
        health_score=analysis.get("health_score"),
        ap_analysis=analysis.get("ap_analysis"),
        client_analysis=analysis.get("client_analysis"),
        recommendations=result.get("recommendations", []),
        full_analysis=analysis,
    )


@router.get("/cache")
async def list_cached_analyses():
    """List available cached analysis files on disk."""
    import glob

    cache_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    pattern = os.path.join(cache_dir, "analysis_cache_*.json")
    files = sorted(glob.glob(pattern), reverse=True)

    entries = []
    for fpath in files[:20]:
        fname = os.path.basename(fpath)
        size = os.path.getsize(fpath)
        entries.append({"filename": fname, "size_bytes": size, "path": fpath})

    return {"caches": entries}
