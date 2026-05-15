"""
notifier.py
Unified notification handler for the trade anomaly pipeline.

Channels:
  - Log file     : logs/pipeline_runs.log  (always on)
  - Terminal     : coloured stdout          (always on)
  - Email        : SMTP via env vars        (on failure AND success)
  - Dashboard    : outputs/dashboard_status.json  (read by the dashboard)

Environment variables for email (put these in a .env file):
    PIPELINE_EMAIL_FROM     sender address
    PIPELINE_EMAIL_TO       recipient address (comma-separated for multiple)
    PIPELINE_SMTP_HOST      e.g. smtp.gmail.com
    PIPELINE_SMTP_PORT      e.g. 587
    PIPELINE_SMTP_USER      usually same as FROM
    PIPELINE_SMTP_PASS      app password or SMTP password
"""

import json
import logging
import os
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path


LOGS_DIR   = Path("logs")
STATUS_FILE = Path("outputs/dashboard_status.json")

ANSI_RESET  = "\033[0m"
ANSI_RED    = "\033[91m"
ANSI_YELLOW = "\033[93m"
ANSI_GREEN  = "\033[92m"
ANSI_CYAN   = "\033[96m"


def _setup_file_logger() -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("pipeline")
    if logger.handlers:
        return logger  # already configured
    logger.setLevel(logging.DEBUG)
    fh = logging.FileHandler(LOGS_DIR / "pipeline_runs.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s"))
    logger.addHandler(fh)
    return logger


class Notifier:
    def __init__(self):
        self._logger = _setup_file_logger()
        self._email_cfg = {
            "from":  os.getenv("PIPELINE_EMAIL_FROM", ""),
            "to":    os.getenv("PIPELINE_EMAIL_TO",   ""),
            "host":  os.getenv("PIPELINE_SMTP_HOST",  "smtp.gmail.com"),
            "port":  int(os.getenv("PIPELINE_SMTP_PORT", "587")),
            "user":  os.getenv("PIPELINE_SMTP_USER",  ""),
            "pass":  os.getenv("PIPELINE_SMTP_PASS",  ""),
        }

    # log + terminal helpers

    def info(self, msg: str):
        self._logger.info(msg)
        print(f"{ANSI_CYAN}{msg}{ANSI_RESET}")

    def warning(self, msg: str):
        self._logger.warning(msg)
        print(f"{ANSI_YELLOW}{msg}{ANSI_RESET}", file=sys.stderr)

    def error(self, msg: str):
        self._logger.error(msg)
        print(f"{ANSI_RED}{msg}{ANSI_RESET}", file=sys.stderr)

    # dashboard JSON

    def update_dashboard(
        self,
        status: str,
        run_id: str = "",
        input_file: str = "",
        failed_step: str = "",
        error: str = "",
        duration_s: float = 0.0,
    ):
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "status":       status,         # "running" | "success" | "failed"
            "run_id":       run_id,
            "input_file":   input_file,
            "failed_step":  failed_step,
            "error":        error,
            "duration_s":   duration_s,
            "updated_at":   datetime.now().isoformat(timespec="seconds"),
        }
        STATUS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # email

    def send_email(self, subject: str, body: str):
        cfg = self._email_cfg
        if not cfg["from"] or not cfg["to"] or not cfg["pass"]:
            self.warning("[NOTIFIER] Email skipped — PIPELINE_EMAIL_* env vars not set.")
            return

        recipients = [r.strip() for r in cfg["to"].split(",") if r.strip()]
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[Trade Pipeline] {subject}"
        msg["From"]    = cfg["from"]
        msg["To"]      = ", ".join(recipients)

        # plain-text part
        msg.attach(MIMEText(body, "plain"))

        # simple HTML part
        html_body = f"""
        <html><body style="font-family:monospace;font-size:14px;color:#1a1a1a;">
        <h2 style="color:{'#c00' if 'FAIL' in subject else '#0a7'};">{subject}</h2>
        <pre style="background:#f5f5f5;padding:12px;border-radius:6px;">{body}</pre>
        <p style="color:#888;font-size:12px;">Sent by trade anomaly pipeline — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        </body></html>
        """
        msg.attach(MIMEText(html_body, "html"))

        try:
            with smtplib.SMTP(cfg["host"], cfg["port"]) as server:
                server.starttls()
                server.login(cfg["user"], cfg["pass"])
                server.sendmail(cfg["from"], recipients, msg.as_string())
            self.info(f"[NOTIFIER] Email sent to {cfg['to']} — {subject}")
        except Exception as exc:
            self.warning(f"[NOTIFIER] Email failed: {exc}")
