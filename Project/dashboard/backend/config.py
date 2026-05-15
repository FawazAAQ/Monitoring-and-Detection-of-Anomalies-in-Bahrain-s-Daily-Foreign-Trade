"""Central config for the dashboard backend.

All runtime settings are loaded from .env (or environment variables) so the
same code runs locally, on a Ministry staging box, or in a container without
source edits.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.resolve()
# Project root is two levels up from backend/: Project/dashboard/backend/ -> Project/
PROJECT_ROOT = BASE_DIR.parent.parent


class Config:
    # Data source — CSVs produced by the notebook pipeline.
    # Default points to <project_root>/outputs/ which is where the
    # notebooks write master_anomalies.csv, CR_LLM.csv, and LLM_Explainability.csv.
    # Override via DATA_DIR in .env if your outputs live elsewhere.
    DATA_DIR = Path(os.getenv("DATA_DIR", PROJECT_ROOT / "outputs"))

    # CSV filenames (must match the notebook outputs exactly)
    MASTER_FILE = "master_anomalies.csv"
    CR_LLM_FILE = "CR_LLM.csv"
    EXPLAIN_FILE = "LLM_Explainability.csv"

    # Flask
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")

    # CORS — React dev servers + self-hosted dashboard. Tighten for production.
    CORS_ORIGINS = [
        "http://127.0.0.1:5000",  # Dashboard served from Flask
        "http://localhost:5000",  # Dashboard served from Flask (localhost alias)
        "http://localhost:5173",  # Vite default
        "http://localhost:3000",  # Create-React-App default
    ]

    # Logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
