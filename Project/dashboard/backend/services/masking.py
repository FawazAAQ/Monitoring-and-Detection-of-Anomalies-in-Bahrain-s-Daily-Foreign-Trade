"""Role-based field masking.

The `active_cr` field is treated as sensitive. Analysts and auditors see a
masked form (`CR-***246` — only the last 3 characters); admins see the full
CR for investigation purposes.

This is a defence-in-depth layer at the API boundary. Step 6 adds Fernet
encryption of the same field at rest (CSV level), so an attacker reading
the disk cannot recover CR values either.
"""
from typing import Optional


FULL_ACCESS_ROLES = {"admin"}


def mask_cr(cr: Optional[str], role: str) -> Optional[str]:
    """Return CR in role-appropriate form.

    - admin:           returns the full value unchanged
    - analyst/auditor: returns last 3 chars prefixed with '***'
    - None input:      returns None
    """
    if cr is None or cr == "":
        return cr
    if role in FULL_ACCESS_ROLES:
        return cr
    tail = str(cr)[-3:] if len(str(cr)) >= 3 else str(cr)
    return f"***{tail}"


def mask_row(row: dict, role: str, cr_fields: tuple[str, ...] = ("active_cr",)) -> dict:
    """Return a copy of `row` with all CR-bearing fields masked for the role."""
    out = dict(row)
    for field in cr_fields:
        if field in out:
            out[field] = mask_cr(out[field], role)
    return out
