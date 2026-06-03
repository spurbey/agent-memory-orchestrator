"""Workflow boundary for closed-session production processing."""

from __future__ import annotations

from typing import Any, Protocol


class PipelineRunner(Protocol):
    def run_next(self, *, lease_seconds: int = 300) -> dict[str, Any]:
        """Run the next leased production job."""


class ClosedSessionPipelineWorkflow:
    """Run production processing for closed sessions through the pipeline service."""

    def __init__(self, pipeline: PipelineRunner) -> None:
        self.pipeline = pipeline

    def run_once(self, *, lease_seconds: int = 300) -> dict[str, Any]:
        return self.pipeline.run_next(lease_seconds=lease_seconds)


__all__ = ["ClosedSessionPipelineWorkflow", "PipelineRunner"]
