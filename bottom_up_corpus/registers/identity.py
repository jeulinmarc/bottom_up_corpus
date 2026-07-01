"""Resolve register specs to canonical entity keys:
- Norway:      orgnr directly, or LEI -> GLEIF registeredAs -> orgnr (digit-strip).
- UK:          ch_number directly (verbatim), or LEI -> GLEIF registeredAs -> ch_number
               (verbatim, only when legalAddress.country == "GB").
- Belgium:     be_number directly (KBO, 10 digits), or LEI -> GLEIF registeredAs ->
               be_number (only when legalAddress.country == "BE").
- Luxembourg:  rcs directly, or LEI -> GLEIF registeredAs -> rcs
               (only when legalAddress.country == "LU").
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


def _norm_rcs(s: str) -> str:
    """Strip whitespace and internal spaces/dots, uppercase.

    LU RCS numbers are ``B`` + digits (e.g. ``"B 60814"`` -> ``"B60814"``).
    The ``B`` prefix is kept; digits are never zero-padded.
    """
    return re.sub(r"[\s.]+", "", s).upper()


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
        # --- LU direct path: rcs provided ---
        if spec.get("rcs"):
            out.append({"rcs": _norm_rcs(str(spec["rcs"])),
                        "lei": spec.get("lei"), "name": spec.get("name", ""),
                        "country": "LU", "status": "ok"})
            continue
        # --- LEI -> GLEIF path (NO, GB, BE, LU) ---
        lei = spec.get("lei")
        orgnr = ch_number = be_number = rcs = name = country = None
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
            elif country == "LU" and ra:
                rcs = _norm_rcs(str(ra))
        if orgnr:
            out.append({"orgnr": orgnr, "lei": lei, "name": name or spec.get("name", ""),
                        "country": country or "", "status": "ok"})
        elif ch_number:
            out.append({"ch_number": ch_number, "lei": lei, "name": name or spec.get("name", ""),
                        "country": country or "", "status": "ok"})
        elif be_number:
            out.append({"be_number": be_number, "lei": lei, "name": name or spec.get("name", ""),
                        "country": country or "", "status": "ok"})
        elif rcs:
            out.append({"rcs": rcs, "lei": lei, "name": name or spec.get("name", ""),
                        "country": country or "", "status": "ok"})
        else:
            out.append({"orgnr": None, "lei": lei, "name": name or spec.get("name", ""),
                        "country": country or "", "status": "unresolved"})
    return out
