"""Structured JSON-lines logging with trace IDs. One file per day of real time in state/logs/."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def new_trace_id(prefix: str = "trace") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class JsonlLogger:
    def __init__(self, logs_dir: Path):
        self.logs_dir = logs_dir

    def log(self, trace_id: str, event: str, **fields: Any) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        record = {"logged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "trace_id": trace_id, "event": event, **fields}
        path = self.logs_dir / f"agent-{datetime.now(timezone.utc):%Y-%m-%d}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
