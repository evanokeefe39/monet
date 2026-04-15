"""Default pipeline — entry → planning → execution with HITL plan approval.

This is the reference multi-graph composition shipped with monet. It
projects the generic :mod:`monet.client._events` core events into typed
domain events (``TriageComplete``, ``PlanReady``, ``WaveComplete`` …)
and exposes typed HITL verbs backed by :meth:`MonetClient.resume`.

Public surface:

- :func:`run` — async generator yielding ``DefaultPipelineEvent``
- :class:`DefaultPipelineRunDetail` — typed view over ``RunDetail``
- Event types: :class:`TriageComplete`, :class:`PlanReady`,
  :class:`PlanApproved`, :class:`PlanInterrupt`, :class:`WaveComplete`,
  :class:`ReflectionComplete`, :class:`ExecutionInterrupt`
- HITL verbs: :func:`approve_plan`, :func:`revise_plan`,
  :func:`reject_plan`, :func:`retry_wave`, :func:`abort_run`
"""

from __future__ import annotations

from monet.pipelines.default._hitl import (
    abort_run,
    approve_plan,
    reject_plan,
    retry_wave,
    revise_plan,
)
from monet.pipelines.default.adapter import continue_after_plan_approval, run
from monet.pipelines.default.events import (
    DefaultInterruptTag,
    DefaultPipelineEvent,
    DefaultPipelineRunDetail,
    ExecutionInterrupt,
    ExecutionInterruptValues,
    ExecutionReviewPayload,
    PlanApprovalPayload,
    PlanApproved,
    PlanInterrupt,
    PlanInterruptValues,
    PlanReady,
    ReflectionComplete,
    TriageComplete,
    WaveComplete,
)

__all__ = [
    "DefaultInterruptTag",
    "DefaultPipelineEvent",
    "DefaultPipelineRunDetail",
    "ExecutionInterrupt",
    "ExecutionInterruptValues",
    "ExecutionReviewPayload",
    "PlanApprovalPayload",
    "PlanApproved",
    "PlanInterrupt",
    "PlanInterruptValues",
    "PlanReady",
    "ReflectionComplete",
    "TriageComplete",
    "WaveComplete",
    "abort_run",
    "approve_plan",
    "continue_after_plan_approval",
    "reject_plan",
    "retry_wave",
    "revise_plan",
    "run",
]
