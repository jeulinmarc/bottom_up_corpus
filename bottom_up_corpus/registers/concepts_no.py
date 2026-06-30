"""Map a Brønnøysund (Brreg) regnskap JSON entry to our curated financial concepts.

Brreg serves structured JSON with named numeric fields (not XBRL), so this is a
direct field->key mapping with fallbacks, over a flatten of the three accounts
blocks (robust to nesting). Leverage is liabilities-based (NGAAP gives total
liabilities, not pure borrowings) — see docs/REGISTER_FINANCIALS.md.
"""
from __future__ import annotations

# curated key -> Brreg leaf field name(s), highest priority first.
NO_FIELDS: dict[str, tuple[str, ...]] = {
    "revenue": ("sumDriftsinntekter", "salgsinntekter"),
    "operating_income": ("driftsresultat",),
    "pretax_income": ("ordinaertResultatFoerSkattekostnad",),
    "income_tax": ("ordinaertResultatSkattekostnad",),
    "net_income": ("aarsresultat",),
    "interest_expense": ("annenRentekostnad", "sumFinanskostnad"),
    "assets": ("sumEiendeler",),
    "assets_current": ("sumOmloepsmidler",),
    "cash": ("sumBankinnskuddOgKontanter",),
    "inventory": ("sumVarer",),
    "receivables": ("sumFordringer",),
    "equity": ("sumEgenkapital",),
    "equity_total": ("sumEgenkapital",),
    "liabilities": ("sumGjeld",),
    "liabilities_current": ("sumKortsiktigGjeld",),
    "short_term_debt": ("sumKortsiktigGjeld",),   # so total_debt ~= total liabilities (NGAAP gearing)
    "long_term_debt": ("sumLangsiktigGjeld",),
}

_BASIS = {"KONSERN": "consolidated", "SELSKAP": "company"}
_BLOCKS = ("resultatregnskapResultat", "eiendeler", "egenkapitalGjeld")


def _leaves(obj) -> dict[str, float]:
    """Recursively collect numeric leaves by field name (last-wins on duplicates)."""
    out: dict[str, float] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                out[k] = v
            elif isinstance(v, (dict, list)):
                out.update(_leaves(v))
    elif isinstance(obj, list):
        for v in obj:
            out.update(_leaves(v))
    return out


def map_brreg_entry(entry: dict) -> dict | None:
    """One Brreg regnskap entry -> {period_end, basis, currency, values}; None if unusable."""
    period = (entry.get("regnskapsperiode") or {}).get("tilDato")
    if not period:
        return None
    flat: dict[str, float] = {}
    for block in _BLOCKS:
        flat.update(_leaves(entry.get(block) or {}))
    currency = (entry.get("valuta") or "").upper() or "NOK"
    basis = _BASIS.get(entry.get("regnskapstype", ""), "company")
    values: dict[str, dict] = {}
    for key, fields in NO_FIELDS.items():
        for fld in fields:
            if fld in flat:
                values[key] = {"value": flat[fld], "unit": currency, "label": key, "tag": fld}
                break
    if not values:
        return None
    return {"period_end": period, "basis": basis, "currency": currency, "values": values}
