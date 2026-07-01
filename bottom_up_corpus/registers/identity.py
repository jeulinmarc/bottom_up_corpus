"""Resolve register specs to canonical entity keys:
- Norway: orgnr directly, or LEI -> GLEIF registeredAs -> orgnr (digit-strip).
- UK:     ch_number directly (verbatim), or LEI -> GLEIF registeredAs -> ch_number
          (verbatim, only when legalAddress.country == "GB").
"""
from __future__ import annotations

_GLEIF = "https://api.gleif.org/api/v1/lei-records/{lei}"


def _norm_ch_number(s: str) -> str:
    """Strip surrounding whitespace, uppercase; left-zero-pad to 8 if all-digits."""
    s = s.strip().upper()
    if s.isdigit():
        return s.zfill(8)
    return s


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
        # --- LEI -> GLEIF path (NO and GB) ---
        lei = spec.get("lei")
        orgnr = ch_number = name = country = None
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
        if orgnr:
            out.append({"orgnr": orgnr, "lei": lei, "name": name or spec.get("name", ""),
                        "country": country or "", "status": "ok"})
        elif ch_number:
            out.append({"ch_number": ch_number, "lei": lei, "name": name or spec.get("name", ""),
                        "country": country or "", "status": "ok"})
        else:
            out.append({"orgnr": None, "lei": lei, "name": name or spec.get("name", ""),
                        "country": country or "", "status": "unresolved"})
    return out
