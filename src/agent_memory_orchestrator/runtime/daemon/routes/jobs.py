"""Daemon production job route names."""

from __future__ import annotations

JOB_ROUTES = ("/api/jobs", "/api/jobs/{job_id}", "/api/jobs/{job_id}/retry")

__all__ = ["JOB_ROUTES"]
