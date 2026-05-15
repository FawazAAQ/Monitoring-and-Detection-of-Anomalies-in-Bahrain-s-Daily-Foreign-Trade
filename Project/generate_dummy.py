"""
Generate a dummy customs trade CSV for pipeline testing.

Output format matches the real Ministry data:
- Date column 'registration_date' formatted as M/D/YYYY 
- Schema includes all columns the cleaning notebook references

Usage:
    python generate_dummy.py                  # generates 2024 with 300 rows
    python generate_dummy.py --year 2023      # different year
    python generate_dummy.py --rows 1000      # different size
"""

import argparse
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd


# REFERENCE POOLS

COUNTRIES = [
    "CN", "US", "AE", "SA", "IN", "JP", "DE", "GB", "TR",
    "OM", "KW", "QA", "EG", "FR", "IT", "KR", "TH", "MY",
]

# Common partner countries (most trade flows through these) vs rare ones
COMMON_PARTNERS = ["CN", "US", "AE", "SA", "IN", "JP", "DE"]
RARE_PARTNERS   = ["TH", "MY", "EG", "TR"]

HS_CATALOG = {
    "8471300000": ("Portable laptop computers",       "NMB", 150, 800),
    "8517120000": ("Cellular telephones",             "NMB", 100, 1200),
    "0102211000": ("Live cattle, pure-bred breeding", "NMB", 800, 2500),
    "2710192100": ("Gas oils for diesel engines",     "LTR", 0.3, 1.2),
    "1006301000": ("Long-grain milled rice",          "KGM", 0.4, 1.5),
    "8703221000": ("Passenger cars 1000-1500cc",      "NMB", 5000, 25000),
    "6109100000": ("Cotton t-shirts",                 "NMB", 2, 15),
    "8504400090": ("Static converters",               "NMB", 30, 250),
    "9018390000": ("Medical syringes and needles",    "NMB", 0.05, 0.50),
    "3923301000": ("Plastic bottles for packaging",   "NMB", 0.10, 0.80),
}
HS_CODES = list(HS_CATALOG.keys())

PRICE_BASIS = ["CIF", "FOB", "EXW"]

CUSTOMS_OFFICES = {
    "2501": "Bahrain International Airport",
    "2601": "Dry Port - Hidd",
    "2701": "Khalifa Bin Salman Port",
    "2801": "King Fahd Causeway",
}

STATUSES = ["Released"] * 8 + ["Held", "Pending"]  # 80% released


def format_date_ministry(d: datetime) -> str:
    """M/D/YYYY with single-digit month/day allowed (matches Ministry format)."""
    return f"{d.month}/{d.day}/{d.year}"


def generate_row(i: int, year: int, rng: np.random.Generator, anomaly_type: str | None) -> dict:
    """Generate one row. anomaly_type seeds intentional anomalies."""

    # Date — random day in the year
    start = datetime(year, 1, 1)
    end = datetime(year, 12, 31)
    day_offset = rng.integers(0, (end - start).days + 1)
    date_obj = start + timedelta(days=int(day_offset))

    # IDs
    office_code = str(rng.choice(list(CUSTOMS_OFFICES.keys())))
    reg_serial = int(rng.integers(1, 10))
    reg_number = 10000 + i
    declaration_id = f"{year}-{office_code}-{reg_serial}-{reg_number}"
    item_id = f"{declaration_id}-1"

    # Goods
    hs = rng.choice(HS_CODES)
    description, default_uom, p_low, p_high = HS_CATALOG[hs]
    uom = default_uom

    # Quantity — varies by UOM
    if uom == "KGM":
        qty = round(float(rng.uniform(100, 5000)), 2)
    elif uom == "LTR":
        qty = round(float(rng.uniform(500, 20000)), 2)
    else:  # NMB
        qty = int(rng.integers(1, 200))

    # Unit price — anomalies overwrite this below
    unit_price = round(float(rng.uniform(p_low, p_high)), 2)

    # Apply seeded anomalies
    if anomaly_type == "underpriced":
        unit_price = round(unit_price * 0.10, 2)  # 90% below market
    elif anomaly_type == "overpriced":
        unit_price = round(unit_price * 5.0, 2)   # 5x above market

    # Geography
    regime_roll = rng.random()
    if regime_roll < 0.65:
        regime = "IM"
    elif regime_roll < 0.95:
        regime = "EX"
    else:
        regime = "RE"

    if anomaly_type == "rare_partner":
        partner = str(rng.choice(RARE_PARTNERS))
    else:
        partner = str(rng.choice(COMMON_PARTNERS, p=[0.30, 0.20, 0.15, 0.12, 0.10, 0.08, 0.05]))

    if regime == "IM":
        origin = partner
        export_c = origin if rng.random() < 0.75 else str(rng.choice(COMMON_PARTNERS))
        destination = "BH"
    elif regime == "EX":
        origin = "BH"
        export_c = "BH"
        destination = partner
    else:  # RE — origin and export differ by definition
        origin = partner
        export_c = "BH"
        destination = str(rng.choice(COMMON_PARTNERS))
        if anomaly_type == "rare_partner":
            destination = str(rng.choice(RARE_PARTNERS))

    # Force a re-export pattern when requested
    if anomaly_type == "reexport":
        origin = str(rng.choice(COMMON_PARTNERS))
        export_c = str(rng.choice([c for c in COMMON_PARTNERS if c != origin]))
        regime = "IM"
        destination = "BH"

    # Money
    invoice_amt = round(qty * unit_price, 2)
    local_amt = round(invoice_amt * 0.376, 2)  # rough USD -> BHD

    # Weights
    if uom == "KGM":
        net_wt = qty
    elif uom == "LTR":
        net_wt = round(qty * float(rng.uniform(0.75, 0.95)), 2)
    else:  # NMB
        net_wt = round(qty * float(rng.uniform(0.5, 8.0)), 2)
    gross_wt = round(net_wt * float(rng.uniform(1.05, 1.20)), 2)

    # CR hashes
    declarant_cr = f"CR_HASH_{int(rng.integers(1, 250)):03d}"
    if regime in ("IM", "RE"):
        consignee_cr = f"CR_HASH_{int(rng.integers(1, 50)):03d}"
        exporter_cr = None
    else:
        consignee_cr = None
        exporter_cr = f"CR_HASH_{int(rng.integers(1, 50)):03d}"

    return {
        "item_id":                     item_id,
        "declaration_id":              declaration_id,
        "year":                        year,
        "customs_office_code":         office_code,
        "customs_office_name":         CUSTOMS_OFFICES[office_code],
        "regime":                      regime,
        "registration_serial":         reg_serial,
        "registration_number":         reg_number,
        "reference_number":            int(rng.integers(10000, 99999)),
        "registration_date":           format_date_ministry(date_obj),
        "hs_code":                     hs,
        "commercial_description":      description,
        "country_of_origin":           origin,
        "country_of_origin_code":      origin,
        "country_of_export":           export_c,
        "country_of_export_code":      export_c,
        "country_of_destination":      destination,
        "country_of_destination_code": destination,
        "uom":                         uom,
        "qty_by_uom":                  qty,
        "actual_unit_price":           unit_price,
        "price_basis":                 str(rng.choice(PRICE_BASIS)),
        "local_amount":                local_amt,
        "sup_amount":                  round(float(rng.uniform(0.5, 5.0)), 2),
        "invoice_amount":              invoice_amt,
        "net_weight":                  net_wt,
        "gross_weight":                gross_wt,
        "status":                      str(rng.choice(STATUSES)),
        "specification_code":          int(rng.integers(1, 10)),
        "warehouse_code":              int(rng.integers(1, 10)),
        "exit_office_code":            office_code,
        "exit_officer_id":             int(rng.integers(100, 999)),
        "operation_name":              None,
        "operation_date":              None,
        "encrypted_declarant_cr":      declarant_cr,
        "encrypted_consignee_cr":      consignee_cr,
        "encrypted_exporter_cr":       exporter_cr,
    }


def generate(year: int, n_rows: int, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)

    # Decide which rows get seeded anomalies (~10% of dataset, mixed types)
    anomaly_assignments: dict[int, str] = {}
    n_anomalies = max(1, n_rows // 10)
    anomaly_indices = rng.choice(n_rows, size=n_anomalies, replace=False)
    anomaly_types = ["underpriced", "overpriced", "rare_partner", "reexport"]
    for idx in anomaly_indices:
        anomaly_assignments[int(idx)] = str(rng.choice(anomaly_types))

    rows = [
        generate_row(i, year, rng, anomaly_assignments.get(i))
        for i in range(n_rows)
    ]
    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--rows", type=int, default=300)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out",  type=str, default=None,
                        help="Output path; defaults to ./dummy_<year>.csv")
    args = parser.parse_args()

    df = generate(args.year, args.rows, args.seed)

    out_path = Path(args.out) if args.out else Path(f"dummy_{args.year}.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)

    print(f"Generated {len(df)} rows x {len(df.columns)} cols")
    print(f"Saved:        {out_path.resolve()}")
    print(f"Date sample:  {df['registration_date'].head(3).tolist()}")
    print(f"Regime mix:   {df['regime'].value_counts().to_dict()}")


if __name__ == "__main__":
    main()
