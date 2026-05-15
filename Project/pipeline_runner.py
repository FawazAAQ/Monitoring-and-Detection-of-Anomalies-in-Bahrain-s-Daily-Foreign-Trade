"""
pipeline_runner.py
Executes the 4-stage trade anomaly pipeline using papermill.

Pipeline structure:
  1. Cleaning
  2. Anomaly_Pipeline
  3. CR_LLM REMOVED
  4. LLM 

Usage:
    python pipeline_runner.py --input data/incoming/ofoq_2024.csv
    python pipeline_runner.py --input data/incoming/ofoq_2024.csv --year 2024
    python pipeline_runner.py --input data/incoming/ofoq_2024.csv --start-from CR_LLM
"""

from dotenv import load_dotenv
load_dotenv()

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import papermill as pm

from notifier import Notifier

NOTEBOOKS_DIR = Path("notebooks")
OUTPUTS_DIR   = Path("outputs/executed")
OUTPUTS_DATA  = Path("outputs")


def build_pipeline(input_path: Path, year: str) -> list:
    """Build the 4-stage pipeline: Cleaning → Anomaly_Pipeline → LLM.

    Cleaning appends new data to cumulative_cleaned.csv and deduplicates.
    All downstream notebooks read cumulative_cleaned.csv so that new data
    is scored against the full historical baseline, not just itself.
    """
    stem           = input_path.stem
    per_file_csv   = str(OUTPUTS_DATA / f"{stem}_cleaned.csv")
    cumulative_csv = str(OUTPUTS_DATA / "cumulative_cleaned.csv")
    master_csv     = str(OUTPUTS_DATA / "master_anomalies.csv")
    cr_llm_csv     = str(OUTPUTS_DATA / "CR_LLM.csv")
    llm_csv        = str(OUTPUTS_DATA / "LLM_Explainability.csv")

    return [
        {
            "name":     "Cleaning",
            "notebook": "Cleaning.ipynb",
            "params": {
                "input_path": str(input_path.resolve()),
                "year":       year,
            },
        },
        {
            "name":     "Anomaly_Pipeline",
            "notebook": "Anomaly_Pipeline.ipynb",
            "params": {
                "input_path":  cumulative_csv,
                "year":        year,
                "output_path": master_csv,
            },
        },
        # CR_LLM stage disabled for the dummy data:
        # {
        #     "name":     "CR_LLM",
        #     "notebook": "CR_LLM.ipynb",
        #     "params": {
        #         "input_path":  cumulative_csv,
        #         "year":        year,
        #         "output_path": cr_llm_csv,
        #     },
        # },
        {
            "name":     "LLM_Explainability",
            "notebook": "LLM.ipynb",
            "params": {
                "input_path":  master_csv,
                "year":        year,
                "output_path": llm_csv,
            },
        },
    ]


def run_pipeline(
    input_path: Path,
    year: str | None = None,
    start_from: str | None = None,
) -> bool:
    notifier = Notifier()
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DATA.mkdir(parents=True, exist_ok=True)

    if not year:
        raw_year = input_path.stem.split("_")[-1]
        if raw_year.isdigit() and len(raw_year) == 2:
            raw_year = "20" + raw_year
        year = raw_year

    pipeline = build_pipeline(input_path, year)

    if start_from:
        names = [s["name"] for s in pipeline]
        if start_from not in names:
            notifier.error(f"Unknown notebook name: {start_from}")
            notifier.error(f"Valid names: {', '.join(names)}")
            return False
        idx      = names.index(start_from)
        pipeline = pipeline[idx:]

    run_id    = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_label = f"[{run_id}]"

    notifier.info(f"{run_label} Pipeline started — input: {input_path.name}, year: {year}")
    notifier.update_dashboard("running", run_id=run_id, input_file=input_path.name)

    start_total = time.time()

    for step in pipeline:
        nb_in  = NOTEBOOKS_DIR / step["notebook"]
        nb_out = OUTPUTS_DIR   / f"{run_id}_{step['notebook']}"

        if not nb_in.exists():
            msg = f"{run_label} Missing notebook: {nb_in}"
            notifier.error(msg)
            notifier.update_dashboard("failed", run_id=run_id, failed_step=step["name"], error=msg)
            notifier.send_email(subject=f"Pipeline FAILED — {step['name']} not found", body=msg)
            return False

        notifier.info(f"{run_label}   Running {step['name']} ...")
        step_start = time.time()

        try:
            pm.execute_notebook(
                str(nb_in),
                str(nb_out),
                parameters=step["params"],
                kernel_name="python3",
                progress_bar=False,
            )
        except pm.PapermillExecutionError as exc:
            elapsed = round(time.time() - step_start, 1)
            msg = (
                f"{run_label} FAILED at {step['name']} after {elapsed}s\n"
                f"Cell {exc.exec_count}: {exc.ename}: {exc.evalue}"
            )
            notifier.error(msg)
            notifier.update_dashboard(
                "failed",
                run_id=run_id,
                failed_step=step["name"],
                error=f"{exc.ename}: {exc.evalue}",
            )
            notifier.send_email(subject=f"Pipeline FAILED — {step['name']}", body=msg)
            return False

        elapsed = round(time.time() - step_start, 1)
        notifier.info(f"{run_label}   {step['name']} done ({elapsed}s)")

    total = round(time.time() - start_total, 1)
    notifier.info(f"{run_label} Pipeline complete in {total}s")
    notifier.update_dashboard("success", run_id=run_id, duration_s=total)
    notifier.send_email(
        subject=f"Pipeline SUCCESS — {input_path.name}",
        body=f"All 4 notebooks completed in {total}s.\nRun ID: {run_id}\nYear: {year}",
    )
    return True


def main():
    parser = argparse.ArgumentParser(description="Trade anomaly pipeline runner")
    parser.add_argument("--input",      required=True, help="Path to raw CSV/Excel file")
    parser.add_argument("--year",       default=None,  help="Dataset year (auto-detected if omitted)")
    parser.add_argument("--start-from", default=None,  help="Notebook name to resume from")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"[ERROR] File not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    success = run_pipeline(input_path, year=args.year, start_from=args.start_from)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
