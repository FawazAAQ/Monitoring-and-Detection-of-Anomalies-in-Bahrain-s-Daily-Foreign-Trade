"""Data loader for the trade-anomaly dashboard.

Reads three CSVs from DATA_DIR:
    master_anomalies.csv      Pre-merged unified flag file (the spine)
    LLM_Explainability.csv    LLM enrichment for flagged rows (one row per item_id)
    CR_LLM.csv                ISIC4 vs HS verdict per CR (one row per CR / per item)

The notebook pipeline now produces master_anomalies.csv with all columns
already present (price + pattern + cr_profile dimensions, final_level via
max-rule, final_reason, final_flag). No synthesis happens here — the loader
just reads, joins LLM enrichment by item_id, joins CR_LLM verdict by
active_cr, coerces declaration_date to datetime, and exposes the merged
view through the same interface the API blueprints already use.
"""
import logging
import math
from pathlib import Path
from threading import Lock
from typing import Any, Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

LEVEL_RANK = {"NORMAL": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}


def _json_safe(value: Any) -> Any:
    """Coerce pandas/numpy values into JSON-serializable Python primitives.

    Browsers' strict JSON.parse rejects NaN/Infinity literals, so we replace
    those with None. Numpy scalar types are converted to native Python so
    Flask's default JSON encoder doesn't choke.
    """
    if value is None:
        return None
    # pandas NaT, NaN, numpy nan
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    # numpy scalars
    if isinstance(value, np.generic):
        v = value.item()
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
        return v
    # pandas Timestamp
    if hasattr(value, "strftime") and hasattr(value, "year"):
        try:
            return value.strftime("%Y-%m-%d")
        except Exception:
            return str(value)
    # bytes
    if isinstance(value, (bytes, bytearray)):
        try:
            return value.decode("utf-8", errors="replace")
        except Exception:
            return str(value)
    # dict / list — recurse
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


class DataStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self._lock = Lock()
        self._frames: dict[str, pd.DataFrame] = {}
        self._merged: pd.DataFrame = pd.DataFrame()
        self._data_date: Optional[str] = None
        self._loaded_mtimes: dict[str, float] = {}
        self.load_all()

    # Loading -----------------------------------------------------------------
    def load_all(self) -> None:
        with self._lock:
            self._frames = {}
            self._loaded_mtimes = {}
            sources = {
                "master": "master_anomalies.csv",
                "llm": "LLM_Explainability.csv",
                "cr_llm": "CR_LLM.csv",
            }
            for key, name in sources.items():
                path = self.data_dir / name
                if path.exists():
                    df = pd.read_csv(path)
                    if "item_id" in df.columns:
                        df["item_id"] = df["item_id"].astype(str)
                    self._frames[key] = df
                    self._loaded_mtimes[key] = path.stat().st_mtime
                    log.info(f"Loaded {key}: {len(df)} rows from {name}")
                else:
                    self._frames[key] = pd.DataFrame()
                    log.warning(f"Missing {key} file: {name}")
            self._build_merged()

    def reload(self) -> None:
        log.info("Reloading all CSVs from disk")
        self.load_all()

    def latest_source_mtime(self) -> float:
        latest = 0.0
        for name in ["master_anomalies.csv", "LLM_Explainability.csv", "CR_LLM.csv"]:
            p = self.data_dir / name
            if p.exists():
                latest = max(latest, p.stat().st_mtime)
        return latest

    def loaded_mtime(self) -> float:
        return max(self._loaded_mtimes.values()) if self._loaded_mtimes else 0.0

    # Merge -------------------------------------------------------------------
    def _build_merged(self) -> None:
        master = self._frames.get("master", pd.DataFrame())
        if master.empty:
            self._merged = pd.DataFrame()
            self._data_date = None
            log.warning("master_anomalies.csv missing or empty — merged view is empty")
            return

        merged = master.copy()

        # Normalise final_level to upper-case strings so downstream comparisons are reliable
        if "final_level" in merged.columns:
            merged["final_level"] = merged["final_level"].fillna("NORMAL").astype(str).str.upper()
        else:
            merged["final_level"] = "NORMAL"

        # Rank for sorting (NORMAL=0..HIGH=3)
        merged["final_level_rank"] = merged["final_level"].map(LEVEL_RANK).fillna(0).astype(int)

        # Flagged indicator — prefer existing final_flag column if present
        if "final_flag" in merged.columns:
            merged["flagged"] = merged["final_flag"].fillna(0).astype(int)
        else:
            merged["flagged"] = (merged["final_level_rank"] > 0).astype(int)

        # Coerce date
        if "declaration_date" in merged.columns:
            merged["declaration_date"] = pd.to_datetime(merged["declaration_date"], errors="coerce")

        # Left-join LLM enrichment for the rows that have it
        llm = self._frames.get("llm", pd.DataFrame())
        if not llm.empty and "item_id" in llm.columns:
            keep = [c for c in [
                "item_id", "llm_explanation", "llm_issue_type", "llm_validation",
                "llm_confidence", "llm_next_step", "llm_validation_reasoning",
                "final_score",
            ] if c in llm.columns]
            merged = merged.merge(llm[keep], on="item_id", how="left", suffixes=("", "_llm"))

        # Synthesise a final_score if the LLM file didn't provide one for this row
        if "final_score" not in merged.columns:
            merged["final_score"] = merged["final_level_rank"] / 3.0
        else:
            merged["final_score"] = merged["final_score"].fillna(merged["final_level_rank"] / 3.0)

        # CR_LLM verdict — joined by active_cr (one row per CR, strongest verdict wins)
        cr_llm = self._frames.get("cr_llm", pd.DataFrame())
        if not cr_llm.empty and "active_cr" in cr_llm.columns:
            risk_col = "gpt4o_risk" if "gpt4o_risk" in cr_llm.columns else None
            if risk_col:
                v = cr_llm[["active_cr", risk_col]].dropna(subset=["active_cr"]).copy()
                v[risk_col] = v[risk_col].astype(str).str.upper()
                v["risk_rank"] = v[risk_col].map(LEVEL_RANK).fillna(0)
                v = v.sort_values("risk_rank", ascending=False).drop_duplicates(subset=["active_cr"], keep="first")
                v = v.rename(columns={risk_col: "cr_llm_risk"})[["active_cr", "cr_llm_risk"]]
                merged = merged.merge(v, on="active_cr", how="left")

        self._merged = merged
        self._data_date = (merged["declaration_date"].max().strftime("%Y-%m-%d")
                           if "declaration_date" in merged.columns and merged["declaration_date"].notna().any()
                           else None)
        log.info(f"Built merged view: {len(merged)} rows ({int(merged['flagged'].sum())} flagged), data_date={self._data_date}")

    # Public accessors --------------------------------------------------------
    @property
    def merged(self) -> pd.DataFrame:
        return self._merged

    @property
    def data_date(self) -> Optional[str]:
        return self._data_date

    def cr_llm_for(self, item_id: str) -> Optional[dict]:
        cr_llm = self._frames.get("cr_llm", pd.DataFrame())
        if cr_llm.empty or "item_id" not in cr_llm.columns:
            return None
        rows = cr_llm[cr_llm["item_id"] == str(item_id)]
        if rows.empty:
            return None
        return _json_safe(rows.iloc[0].to_dict())

    def llm_for(self, item_id: str) -> Optional[dict]:
        llm = self._frames.get("llm", pd.DataFrame())
        if llm.empty or "item_id" not in llm.columns:
            return None
        rows = llm[llm["item_id"] == str(item_id)]
        if rows.empty:
            return None
        return _json_safe(rows.iloc[0].to_dict())

    def get_anomaly(self, item_id: str) -> Optional[dict]:
        item_id = str(item_id)
        if self._merged.empty:
            return None
        rows = self._merged[self._merged["item_id"] == item_id]
        if rows.empty:
            return None
        row = _json_safe(rows.iloc[0].to_dict())
        row["_cr_llm_raw"] = self.cr_llm_for(item_id)
        row["_llm_raw"] = self.llm_for(item_id)
        return row

    # Summary, donut, timeseries ---------------------------------------------
    def summary_stats(self) -> dict:
        df = self._merged
        if df.empty:
            return {
                "total_rows": 0, "flagged": 0, "data_date": self._data_date,
                "by_final_level": {}, "by_year_month": {}, "by_trade_type": {},
                "unique_crs": 0, "llm_explanations_available": 0,
            }
        flagged_only = df[df["flagged"] == 1]
        return {
            "total_rows": int(len(df)),
            "flagged": int(flagged_only.shape[0]),
            "data_date": self._data_date,
            "by_final_level": flagged_only["final_level"].value_counts().to_dict(),
            "by_year_month": (df["year_month"].value_counts().sort_index().to_dict()
                              if "year_month" in df.columns else {}),
            "by_trade_type": (df["trade_type"].value_counts().to_dict()
                              if "trade_type" in df.columns else {}),
            "unique_crs": int(df["active_cr"].nunique()) if "active_cr" in df.columns else 0,
            "llm_explanations_available": int(self._frames.get("llm", pd.DataFrame()).shape[0]),
        }

    def donut_data(self) -> dict:
        df = self._merged
        if df.empty:
            return {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "NORMAL": 0, "total_flagged": 0}
        c = df["final_level"].value_counts().to_dict()
        flagged = df[df["flagged"] == 1]
        return {
            "HIGH": int(c.get("HIGH", 0)),
            "MEDIUM": int(c.get("MEDIUM", 0)),
            "LOW": int(c.get("LOW", 0)),
            "NORMAL": int(c.get("NORMAL", 0)),
            "total_flagged": int(flagged.shape[0]),
        }

    def timeseries(self, year_month: Optional[str] = None) -> dict:
        df = self._merged
        if df.empty or "declaration_date" not in df.columns:
            return {"year_month": year_month, "days": []}
        if year_month is None:
            year_month = self._data_date[:7] if self._data_date else None
        if not year_month:
            return {"year_month": None, "days": []}
        try:
            ym_start = pd.to_datetime(year_month + "-01")
        except Exception:
            return {"year_month": year_month, "days": []}
        ym_end = (ym_start + pd.offsets.MonthEnd(0))
        in_month = df[(df["declaration_date"] >= ym_start) & (df["declaration_date"] <= ym_end)].copy()
        in_month["day"] = in_month["declaration_date"].dt.day
        days_in_month = ym_end.day
        out = []
        for d in range(1, days_in_month + 1):
            sd = in_month[in_month["day"] == d]
            high = int((sd["final_level"] == "HIGH").sum())
            medium = int((sd["final_level"] == "MEDIUM").sum())
            low = int((sd["final_level"] == "LOW").sum())
            out.append({
                "date": (ym_start + pd.Timedelta(days=d - 1)).strftime("%Y-%m-%d"),
                "day": d, "high": high, "medium": medium, "low": low,
                "total": high + medium + low,
            })
        return {"year_month": year_month, "days": out}

    # CR analysis -------------------------------------------------------------
    def top_crs(self, sort: str = "count", limit: int = 20, min_anomalies: int = 1) -> list[dict]:
        df = self._merged
        if df.empty or "active_cr" not in df.columns:
            return []
        flagged = df[df["flagged"] == 1].copy()
        if flagged.empty:
            return []

        rows = []
        for cr, sub in flagged.groupby("active_cr"):
            if pd.isna(cr) or cr == "":
                continue
            high_n = int((sub["final_level"] == "HIGH").sum())
            med_n = int((sub["final_level"] == "MEDIUM").sum())
            low_n = int((sub["final_level"] == "LOW").sum())
            count = int(len(sub))
            if count < min_anomalies:
                continue

            partner_col = ("partner_country_code" if "partner_country_code" in sub.columns
                           else ("country_of_origin" if "country_of_origin" in sub.columns else None))
            top_partners = []
            if partner_col:
                vc = sub[partner_col].dropna().astype(str).value_counts().head(3)
                top_partners = [{"code": k, "count": int(v)} for k, v in vc.items()]

            hs_col = "hs6" if "hs6" in sub.columns else ("hs_code" if "hs_code" in sub.columns else None)
            top_hs = []
            if hs_col:
                vc = sub[hs_col].dropna().astype(str).value_counts().head(3)
                hs_desc_col = "hs_desc" if "hs_desc" in sub.columns else None
                for code, n in vc.items():
                    desc = ""
                    if hs_desc_col:
                        m = sub[sub[hs_col].astype(str) == code][hs_desc_col].dropna()
                        desc = str(m.iloc[0]) if not m.empty else ""
                    top_hs.append({"code": code, "count": int(n), "desc": desc})

            latest_date = (sub["declaration_date"].max().strftime("%Y-%m-%d")
                           if "declaration_date" in sub.columns and sub["declaration_date"].notna().any() else None)
            cr_llm_risk = (str(sub["cr_llm_risk"].dropna().iloc[0])
                           if "cr_llm_risk" in sub.columns and sub["cr_llm_risk"].notna().any() else None)

            rows.append({
                "active_cr": str(cr),
                "anomaly_count": count,
                "high_count": high_n, "medium_count": med_n, "low_count": low_n,
                "latest_flag_date": latest_date,
                "top_partners": top_partners, "top_hs_codes": top_hs,
                "cr_llm_risk": cr_llm_risk,
            })

        if sort == "high":
            rows.sort(key=lambda r: (-r["high_count"], -r["anomaly_count"]))
        else:
            rows.sort(key=lambda r: (-r["anomaly_count"], -r["high_count"]))
        return rows[:limit]

    def cr_detail(self, cr: str) -> Optional[dict]:
        df = self._merged
        if df.empty or "active_cr" not in df.columns:
            return None
        sub = df[df["active_cr"].astype(str) == str(cr)]
        if sub.empty:
            return None
        flagged_sub = sub[sub["flagged"] == 1]

        partner_col = "partner_country_code" if "partner_country_code" in sub.columns else "country_of_origin"
        hs_col = "hs6" if "hs6" in sub.columns else "hs_code"

        partners_full = []
        if partner_col in sub.columns:
            vc = flagged_sub[partner_col].dropna().astype(str).value_counts()
            partners_full = [{"code": k, "count": int(v)} for k, v in vc.items()]

        hs_full = []
        if hs_col in sub.columns:
            vc = flagged_sub[hs_col].dropna().astype(str).value_counts()
            for code, n in vc.items():
                desc = ""
                if "hs_desc" in sub.columns:
                    m = sub[sub[hs_col].astype(str) == code]["hs_desc"].dropna()
                    desc = str(m.iloc[0]) if not m.empty else ""
                hs_full.append({"code": code, "count": int(n), "desc": desc})

        item_cols = [c for c in [
            "item_id", "declaration_date", "hs_code", "hs_desc",
            "trade_type", "partner_country_code",
            "final_level", "final_reason", "final_score",
        ] if c in flagged_sub.columns]
        items = flagged_sub[item_cols].copy()
        if "declaration_date" in items.columns:
            items["declaration_date"] = items["declaration_date"].dt.strftime("%Y-%m-%d")

        return {
            "active_cr": str(cr),
            "total_rows": int(len(sub)),
            "flagged_count": int(len(flagged_sub)),
            "high_count": int((flagged_sub["final_level"] == "HIGH").sum()),
            "medium_count": int((flagged_sub["final_level"] == "MEDIUM").sum()),
            "low_count": int((flagged_sub["final_level"] == "LOW").sum()),
            "partners": partners_full,
            "hs_codes": hs_full,
            "items": [_json_safe(r) for r in items.to_dict(orient="records")],
            "cr_llm_risk": (str(sub["cr_llm_risk"].dropna().iloc[0])
                            if "cr_llm_risk" in sub.columns and sub["cr_llm_risk"].notna().any()
                            else None),
        }

    # Anomalies list ----------------------------------------------------------
    def list_anomalies(
        self,
        page: int = 1,
        page_size: int = 25,
        flag_levels: Optional[list[str]] = None,
        year_month: Optional[str] = None,
        trade_type: Optional[str] = None,
        search: Optional[str] = None,
        sort_by: str = "final_score",
        sort_dir: str = "desc",
    ) -> dict:
        df = self._merged
        if df.empty:
            return {"items": [], "total": 0, "page": page, "page_size": page_size, "pages": 0}

        if flag_levels is None:
            flag_levels = ["LOW", "MEDIUM", "HIGH"]
        if flag_levels and "final_level" in df.columns:
            df = df[df["final_level"].isin(flag_levels)]
        if year_month and "year_month" in df.columns:
            df = df[df["year_month"].astype(str) == year_month]
        if trade_type and "trade_type" in df.columns:
            df = df[df["trade_type"] == trade_type]

        if search:
            needle = search.lower()
            cols = ["item_id", "active_cr", "hs_code", "hs_desc",
                    "country_of_origin", "country_of_destination", "partner_country_code"]
            masks = []
            for col in cols:
                if col in df.columns:
                    masks.append(df[col].astype(str).str.lower().str.contains(needle, na=False))
            if masks:
                m = masks[0]
                for mm in masks[1:]:
                    m = m | mm
                df = df[m]

        allowed_sort = {"final_score", "final_level_rank", "year", "year_month", "item_id", "declaration_date"}
        if sort_by not in allowed_sort or sort_by not in df.columns:
            sort_by = "final_score" if "final_score" in df.columns else "final_level_rank"
        ascending = (sort_dir or "desc").lower() != "desc"
        df = df.sort_values(by=sort_by, ascending=ascending, kind="stable")

        total = len(df)
        pages = max(1, (total + page_size - 1) // page_size)
        page = max(1, min(page, pages))
        start = (page - 1) * page_size
        end = start + page_size

        slim = [c for c in [
            "item_id", "declaration_date", "year_month",
            "trade_type", "active_cr",
            "hs_code", "hs6", "hs_desc",
            "country_of_origin", "country_of_destination", "partner_country_code",
            "uom", "qty_by_uom", "actual_unit_price",
            "price_level", "pattern_level", "cr_profile_level",
            "final_score", "final_level", "final_reason",
        ] if c in df.columns]
        out_df = df.iloc[start:end][slim].copy()
        if "declaration_date" in out_df.columns:
            out_df["declaration_date"] = out_df["declaration_date"].dt.strftime("%Y-%m-%d")
        items = [_json_safe(r) for r in out_df.to_dict(orient="records")]
        return {"items": items, "total": int(total), "page": page,
                "page_size": page_size, "pages": int(pages)}
