"""Unified root entrypoint for SocraticQ (FastAPI + Edge Web UI + RawTree Cortex).

Run with:
    uvicorn server:app --host 0.0.0.0 --port 8000
"""

from agent_rawtree.web_app import app

__all__ = ["app"]
