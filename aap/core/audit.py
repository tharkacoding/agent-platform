"""Append-only audit log of every tool call, approval, file write and network request."""
from __future__ import annotations

import logging

from .. import registry

_logger = logging.getLogger("aap.audit")


def log(run_id: str, agent_id: str, kind: str, data: dict) -> None:
    try:
        registry.add_audit(run_id, agent_id, kind, data)
    except Exception:  # noqa: BLE001 - auditing must never break a run
        _logger.exception("audit write failed")
