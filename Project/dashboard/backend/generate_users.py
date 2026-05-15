"""Generate users.json with bcrypt-hashed demo passwords.

Run once:
    python generate_users.py

Two roles:
  - admin: full view (unhashed CRs, audit log, snapshot downloads)
  - user:  hashed CRs, no audit log access, no snapshot downloads
"""
import json
from pathlib import Path

from services.security import hash_password

DEMO_USERS = [
    {
        "employee_id": "AD-001",
        "name": "Khalid Al-Rashid",
        "role": "admin",
        "password": "admin123",
    },
    {
        "employee_id": "US-001",
        "name": "Fatima Al-Sayed",
        "role": "user",
        "password": "user123",
    },
]


def main():
    out = []
    for u in DEMO_USERS:
        out.append({
            "employee_id": u["employee_id"],
            "name": u["name"],
            "role": u["role"],
            "password_hash": hash_password(u["password"]),
        })

    path = Path(__file__).parent / "users.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print(f"Wrote {len(out)} users to {path}")
    print("\nDemo credentials (CHANGE BEFORE DEPLOYMENT):")
    for u in DEMO_USERS:
        print(f"  {u['employee_id']:<8} / {u['password']:<12} ({u['role']})")


if __name__ == "__main__":
    main()
