"""
watcher.py
Monitors a drop folder for new CSV or Excel files.
When a new file appears, it waits for the write to finish,
then calls run_pipeline() automatically.

Usage:
    python watcher.py                        # watches ./data/incoming by default
    python watcher.py --watch data/incoming
    python watcher.py --watch data/incoming --stable-secs 5
"""

import argparse
import os
import sys
import time
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from notifier import Notifier
from pipeline_runner import run_pipeline

WATCHED_EXTENSIONS = {".csv", ".xlsx", ".xls"}


def file_is_stable(path: Path, stable_secs: int = 3) -> bool:
    """
    Returns True when the file size hasn't changed for stable_secs seconds.
    Handles the case where a large file is still being written.
    """
    prev_size = -1
    for _ in range(stable_secs * 2):
        try:
            current_size = path.stat().st_size
        except FileNotFoundError:
            return False
        if current_size == prev_size and current_size > 0:
            return True
        prev_size = current_size
        time.sleep(0.5)
    return False


class DropFolderHandler(FileSystemEventHandler):
    def __init__(self, stable_secs: int = 3):
        self.stable_secs = stable_secs
        self.notifier    = Notifier()
        self.in_progress = set()  # prevents duplicate triggers

    def on_created(self, event: FileSystemEvent):
        if event.is_directory:
            return

        path = Path(event.src_path)
        if path.suffix.lower() not in WATCHED_EXTENSIONS:
            return
        if path in self.in_progress:
            return

        self.notifier.info(f"[WATCHER] Detected new file: {path.name}")
        self.in_progress.add(path)

        if not file_is_stable(path, self.stable_secs):
            self.notifier.warning(f"[WATCHER] File {path.name} never stabilised — skipping.")
            self.in_progress.discard(path)
            return

        self.notifier.info(f"[WATCHER] File stable. Starting pipeline for {path.name} ...")
        run_pipeline(path)
        self.in_progress.discard(path)


def watch(folder: Path, stable_secs: int = 3):
    folder.mkdir(parents=True, exist_ok=True)
    notifier = Notifier()
    notifier.info(f"[WATCHER] Watching: {folder.resolve()}")
    notifier.info(f"[WATCHER] Accepted extensions: {', '.join(WATCHED_EXTENSIONS)}")
    notifier.info("[WATCHER] lose Tab to stop.\n")

    handler  = DropFolderHandler(stable_secs=stable_secs)
    observer = Observer()
    observer.schedule(handler, str(folder), recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        notifier.info("[WATCHER] Stopping.")
        observer.stop()

    observer.join()


def main():
    parser = argparse.ArgumentParser(description="Drop-folder watcher for trade anomaly pipeline")
    parser.add_argument("--watch",       default="data/incoming",
                        help="Folder to monitor (default: data/incoming)")
    parser.add_argument("--stable-secs", type=int, default=3,
                        help="Seconds of stable file size before triggering (default: 3)")
    args = parser.parse_args()

    watch(Path(args.watch), stable_secs=args.stable_secs)


if __name__ == "__main__":
    main()
