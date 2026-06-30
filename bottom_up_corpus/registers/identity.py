"""Resolve register specs to (orgnr, lei): orgnr directly, or LEI -> GLEIF
registeredAs -> orgnr (Norway only, no-guess)."""
from __future__ import annotations

_GLEIF = "https://api.gleif.org/api/v1/lei-records/{lei}"


def resolve_register_specs(specs: list[dict], *, fetcher) -> list[dict]:
    out: list[dict] = []
    for spec in specs:
        if spec.get("orgnr"):
            out.append({"orgnr": "".join(ch for ch in str(spec["orgnr"]) if ch.isdigit()),
                        "lei": spec.get("lei"), "name": spec.get("name", ""),
                        "country": "NO", "status": "ok"})
            continue
        lei = spec.get("lei")
        orgnr = name = country = None
        if lei:
            try:
                ent = (fetcher.get_json(_GLEIF.format(lei=lei)).get("data", {})
                       .get("attributes", {}).get("entity", {}))
                country = (ent.get("legalAddress") or {}).get("country")
                name = (ent.get("legalName") or {}).get("name", "")
                ra = ent.get("registeredAs")
                if country == "NO" and ra:
                    orgnr = "".join(ch for ch in str(ra) if ch.isdigit())
            except Exception:  # noqa: BLE001
                pass
        out.append({"orgnr": orgnr, "lei": lei, "name": name or spec.get("name", ""),
                    "country": country or "", "status": "ok" if orgnr else "unresolved"})
    return out
