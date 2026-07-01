"""Resolve register specs to canonical entity keys:
- Norway:      orgnr directly, or LEI -> GLEIF registeredAs -> orgnr (digit-strip).
- UK:          ch_number directly (verbatim), or LEI -> GLEIF registeredAs -> ch_number
               (verbatim, only when legalAddress.country == "GB").
- Belgium:     be_number directly (KBO, 10 digits), or LEI -> GLEIF registeredAs ->
               be_number (only when legalAddress.country == "BE").
- Finland:     business_id directly (Y-tunnus NNNNNNN-N), or LEI -> GLEIF registeredAs ->
               business_id (only when legalAddress.country == "FI").
- Luxembourg:  rcs directly, or LEI -> GLEIF registeredAs -> rcs
               (only when legalAddress.country == "LU").
- Estonia:     registrikood directly (8 digits), or LEI -> GLEIF registeredAs ->
               registrikood (only when legalAddress.country == "EE").
"""
from __future__ import annotations
import re

_GLEIF = "https://api.gleif.org/api/v1/lei-records/{lei}"


def _norm_ch_number(s: str) -> str:
    """Strip surrounding whitespace, uppercase; left-zero-pad to 8 if all-digits."""
    s = s.strip().upper()
    if s.isdigit():
        return s.zfill(8)
    return s


def _norm_kbo(s: str) -> str:
    """Strip non-digit characters; preserve leading zero; zero-pad to 10 digits."""
    return re.sub(r"\D", "", s).zfill(10)


def _norm_ytunnus(s: str) -> str:
    """Strip surrounding whitespace; keep Y-tunnus (NNNNNNN-N) format as-is."""
    return s.strip()


def _norm_rcs(s: str) -> str:
    """Strip whitespace and internal spaces/dots, uppercase.

    LU RCS numbers are ``B`` + digits (e.g. ``"B 60814"`` -> ``"B60814"``).
    The ``B`` prefix is kept; digits are never zero-padded.
    """
    return re.sub(r"[\s.]+", "", s).upper()


def _norm_registrikood(s: str) -> str:
    """Strip non-digit characters; left-pad to 8 digits (EE registry code)."""
    return re.sub(r"\D", "", str(s)).zfill(8)


def resolve_register_specs(specs: list[dict], *, fetcher) -> list[dict]:
    out: list[dict] = []
    for spec in specs:
        # --- GB direct path: ch_number provided verbatim ---
        if spec.get("ch_number"):
            out.append({"ch_number": _norm_ch_number(str(spec["ch_number"])),
                        "lei": spec.get("lei"), "name": spec.get("name", ""),
                        "country": "GB", "status": "ok"})
            continue
        # --- NO direct path: orgnr provided ---
        if spec.get("orgnr"):
            out.append({"orgnr": "".join(ch for ch in str(spec["orgnr"]) if ch.isdigit()),
                        "lei": spec.get("lei"), "name": spec.get("name", ""),
                        "country": "NO", "status": "ok"})
            continue
        # --- BE direct path: be_number (KBO) provided ---
        if spec.get("be_number"):
            out.append({"be_number": _norm_kbo(str(spec["be_number"])),
                        "lei": spec.get("lei"), "name": spec.get("name", ""),
                        "country": "BE", "status": "ok"})
            continue
        # --- FI direct path: business_id (Y-tunnus) provided ---
        if spec.get("business_id"):
            out.append({"business_id": _norm_ytunnus(str(spec["business_id"])),
                        "lei": spec.get("lei"), "name": spec.get("name", ""),
                        "country": "FI", "status": "ok"})
            continue
        # --- LU direct path: rcs provided ---
        if spec.get("rcs"):
            out.append({"rcs": _norm_rcs(str(spec["rcs"])),
                        "lei": spec.get("lei"), "name": spec.get("name", ""),
                        "country": "LU", "status": "ok"})
            continue
        # --- EE direct path: registrikood provided ---
        if spec.get("registrikood"):
            out.append({"registrikood": _norm_registrikood(str(spec["registrikood"])),
                        "lei": spec.get("lei"), "name": spec.get("name", ""),
                        "country": "EE", "status": "ok"})
            continue
        # --- LEI -> GLEIF path (NO, GB, BE, FI, LU, EE) ---
        lei = spec.get("lei")
        orgnr = ch_number = be_number = business_id = rcs = registrikood = name = country = None
        if lei:
            try:
                raw = fetcher.get_json(_GLEIF.format(lei=lei))
            except Exception:  # noqa: BLE001 — GLEIF network/HTTP failure -> unresolved
                raw = {}
            ent = (raw.get("data") or {}).get("attributes", {}).get("entity", {})
            country = (ent.get("legalAddress") or {}).get("country")
            name = (ent.get("legalName") or {}).get("name", "")
            ra = ent.get("registeredAs")
            if country == "NO" and ra:
                orgnr = "".join(ch for ch in str(ra) if ch.isdigit())
            elif country == "GB" and ra:
                ch_number = _norm_ch_number(str(ra))
            elif country == "BE" and ra:
                be_number = _norm_kbo(str(ra))
            elif country == "FI" and ra:
                business_id = _norm_ytunnus(str(ra))
            elif country == "LU" and ra:
                rcs = _norm_rcs(str(ra))
            elif country == "EE" and ra:
                registrikood = _norm_registrikood(str(ra))
        if orgnr:
            out.append({"orgnr": orgnr, "lei": lei, "name": name or spec.get("name", ""),
                        "country": country or "", "status": "ok"})
        elif ch_number:
            out.append({"ch_number": ch_number, "lei": lei, "name": name or spec.get("name", ""),
                        "country": country or "", "status": "ok"})
        elif be_number:
            out.append({"be_number": be_number, "lei": lei, "name": name or spec.get("name", ""),
                        "country": country or "", "status": "ok"})
        elif business_id:
            out.append({"business_id": business_id, "lei": lei, "name": name or spec.get("name", ""),
                        "country": country or "", "status": "ok"})
        elif rcs:
            out.append({"rcs": rcs, "lei": lei, "name": name or spec.get("name", ""),
                        "country": country or "", "status": "ok"})
        elif registrikood:
            out.append({"registrikood": registrikood, "lei": lei,
                        "name": name or spec.get("name", ""),
                        "country": country or "", "status": "ok"})
        else:
            out.append({"orgnr": None, "lei": lei, "name": name or spec.get("name", ""),
                        "country": country or "", "status": "unresolved"})
    return out
