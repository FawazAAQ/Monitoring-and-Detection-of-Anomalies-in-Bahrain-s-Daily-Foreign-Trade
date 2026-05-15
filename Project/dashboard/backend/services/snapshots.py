"""Snapshot service.

Each Refresh archives the current state of the source CSVs to
DATA_DIR/_snapshots/<data_date>/ before reloading. Snapshot directories
are named by the data_date (latest declaration_date in the data they
contain) so the history page shows real business dates, not refresh times.
"""
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

SNAPSHOTS_DIRNAME = "_snapshots"
MANIFEST_NAME = "manifest.json"

# After v5: master_anomalies.csv replaces price_anomaly_output / CR_Profile_Anomaly /
# Trade_Pattern_Anomaly. The LLM outputs are unchanged.
TRACKED_FILES = [
    "master_anomalies.csv",
    "LLM_Explainability.csv",
    "CR_LLM.csv",
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SnapshotService:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.snap_root = self.data_dir / SNAPSHOTS_DIRNAME
        self.snap_root.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.snap_root / MANIFEST_NAME

    def _load_manifest(self) -> list[dict]:
        if not self.manifest_path.exists():
            return []
        try:
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning(f"Manifest unreadable, starting fresh: {e}")
            return []

    def _save_manifest(self, entries: list[dict]) -> None:
        self.manifest_path.write_text(
            json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8",
        )

    def list_snapshots(self) -> list[dict]:
        return sorted(self._load_manifest(), key=lambda e: e.get("data_date") or "", reverse=True)

    def find_snapshot(self, snapshot_id: str) -> Optional[dict]:
        for e in self._load_manifest():
            if e.get("id") == snapshot_id:
                return e
        return None

    def archive_current(
        self,
        data_date: Optional[str],
        archived_by: Optional[str],
        summary_stats: Optional[dict] = None,
    ) -> Optional[dict]:
        if data_date is None:
            label = f"unknown_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        else:
            label = str(data_date)

        target = self.snap_root / label
        target.mkdir(parents=True, exist_ok=True)

        copied = []
        for fname in TRACKED_FILES:
            src = self.data_dir / fname
            if src.exists():
                shutil.copy2(src, target / fname)
                copied.append(fname)

        if not copied:
            try:
                target.rmdir()
            except OSError:
                pass
            return None

        entry = {
            "id": label,
            "data_date": data_date,
            "archived_at": _utc_now_iso(),
            "archived_by": archived_by,
            "files": copied,
            "summary": summary_stats or {},
        }
        manifest = [e for e in self._load_manifest() if e.get("id") != label]
        manifest.append(entry)
        self._save_manifest(manifest)
        log.info(f"Archived snapshot id={label} by={archived_by} ({len(copied)} files)")
        return entry

    def snapshot_dir(self, snapshot_id: str) -> Optional[Path]:
        d = self.snap_root / snapshot_id
        return d if d.exists() else None
