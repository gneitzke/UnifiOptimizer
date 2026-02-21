"""FastAPI application — main entry point for the web backend."""

import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Ensure project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.routers import analysis, auth, repair
from server.services.session_manager import session_pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    yield
    # Clean up sessions on shutdown
    session_pool.clear_all()


app = FastAPI(
    title="UniFi Optimizer API",
    description="Network analysis and optimization API for Ubiquiti UniFi controllers.",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS — allow the Vite dev server and any Cloudflare Pages origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(analysis.router, prefix="/api/analysis", tags=["analysis"])
app.include_router(repair.router, prefix="/api/repair", tags=["repair"])


@app.get("/api/health")
async def health_check():
    """Simple health check."""
    return {"status": "ok", "version": "2.0.0"}


# Serve the built React frontend as static files (in production)
web_dist = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "dist")
if os.path.isdir(web_dist):
    app.mount("/", StaticFiles(directory=web_dist, html=True), name="frontend")
