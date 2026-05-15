"""Append-only audit logger.

Writes one JSON object per line to audit.log in the backend folder.
Format is line-delimited JSON (jsonl) so it's greppable, diffable, and
easy to load into pandas for the booklet / demo write-up.

Events logged:
- auth.login_success / auth.login_failure
- auth.logout
- data.reload
- Future: record.view (Step 3)
"""
import json
import logging
import time
from pathlib import Path
from threading import Lock
from typing import Optional

log = logging.getLogger(__name__)

_lock = Lock()


class AuditLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: str, actor: Optional[str], ip: Optional[str], details: Optional[dict] = None) -> None:
        entry = {
            "ts": int(time.time()),
            "event": event,
            "actor": actor,
            "ip": ip,
            "details": details or {},
        }
        line = json.dumps(entry, ensure_ascii=False)
        try:
            with _lock:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except OSError as e:
            log.error(f"Failed to write audit entry: {e}")
