"""Shared REST API endpoint constants.

Used by server, client, and worker to avoid hardcoded URL duplication.
Template strings contain FastAPI-compatible path-parameter placeholders,
(e.g. ``{eid}``, ``{wid}``) and can be formatted with ``.format()`` on the
client side when variable values are needed.
"""

from __future__ import annotations

__all__ = [
    "ENDPOINT_EXPERIMENT",
    "ENDPOINT_EXPERIMENTS",
    "ENDPOINT_EXPERIMENT_ARCHIVE",
    "ENDPOINT_RUN",
    "ENDPOINT_RUNS_DISPATCH",
    "ENDPOINT_RUNS_ERROR",
    "ENDPOINT_RUNS_RESULT",
    "ENDPOINT_RUN_RESET",
    "ENDPOINT_RUN_RESULTS",
    "ENDPOINT_WORKER",
    "ENDPOINT_WORKERS",
    "ENDPOINT_WORKERS_DEAD",
    "ENDPOINT_WORKER_CHECK_IN",
    "ENDPOINT_WORKER_REVIVE",
]

# ── experiments ────────────────────────────────────────────────────────────────
ENDPOINT_EXPERIMENTS = "/experiments"
ENDPOINT_EXPERIMENT = "/experiments/{eid}"
ENDPOINT_EXPERIMENT_ARCHIVE = "/experiments/{eid}/archive"

# ── runs ───────────────────────────────────────────────────────────────────────
ENDPOINT_RUNS_DISPATCH = "/runs/dispatch"
ENDPOINT_RUNS_RESULT = "/runs/result"
ENDPOINT_RUNS_ERROR = "/runs/error"
ENDPOINT_RUN = "/runs/{eid}/{index}/{iteration}"
ENDPOINT_RUN_RESULTS = "/runs/{eid}/{index}/{iteration}/results"
ENDPOINT_RUN_RESET = "/runs/{eid}/{index}/{iteration}/reset"

# ── workers ────────────────────────────────────────────────────────────────────
ENDPOINT_WORKERS = "/workers"
ENDPOINT_WORKER = "/workers/{wid}"
ENDPOINT_WORKER_CHECK_IN = "/workers/check_in/{wid}"
ENDPOINT_WORKER_REVIVE = "/workers/revive/{wid}"
ENDPOINT_WORKERS_DEAD = "/workers/dead"
