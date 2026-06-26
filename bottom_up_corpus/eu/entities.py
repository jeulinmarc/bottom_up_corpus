"""European issuer identity resolution (ticker / ISIN / LEI / name -> Entity).

Mirrors the US name->CIK resolver's multi-tier approach: try the most specific
identifier first, record the resolution tier as provenance, never guess an
ambiguous match.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

GLEIF = "https://api.gleif.org/api/v1/lei-records"


@dataclass(frozen=True)
class Entity:
    lei: str | None
    name: str
    country: str
    isins: tuple[str, ...] = ()
    tickers: tuple[str, ...] = ()
    resolution: str = ""  # "lei" | "isin" | "ticker" | "name" | "unresolved"


def _from_gleif_record(attrs: dict) -> tuple[str, str, str]:
    ent = attrs.get("entity", {})
    name = (ent.get("legalName") or {}).get("name", "")
    country = ((ent.get("legalAddress") or {}).get("country")
               or (ent.get("headquartersAddress") or {}).get("country") or "")
    return attrs.get("lei", ""), name, country


def _lookup_lei(lei: str, fetcher) -> Entity | None:
    try:
        data = fetcher.get_json(f"{GLEIF}/{lei}").get("data")
    except Exception:
        return None
    if not data:
        return None
    lei_v, name, country = _from_gleif_record(data["attributes"])
    return Entity(lei=lei_v or lei, name=name, country=country, resolution="lei")


def _lookup_isin(isin: str, fetcher) -> Entity:
    url = f"{GLEIF}?filter%5Bisin%5D={quote(isin)}&page%5Bsize%5D=1"
    try:
        rows = fetcher.get_json(url).get("data") or []
    except Exception:
        rows = []
    if rows:
        lei_v, nm, ctry = _from_gleif_record(rows[0]["attributes"])
        return Entity(lei=lei_v, name=nm, country=ctry, resolution="isin")
    return Entity(lei=None, name="", country="", resolution="unresolved")


def _lookup_name(name: str, country: str, fetcher) -> Entity:
    url = f"{GLEIF}?filter%5Bentity.legalName%5D={quote(name)}&page%5Bsize%5D=10"
    try:
        rows = fetcher.get_json(url).get("data") or []
    except Exception:
        rows = []
    # If a country is given, keep only rows matching that country.
    if country:
        rows = [
            r for r in rows
            if _from_gleif_record(r["attributes"])[2] == country
        ]
    # Resolve ONLY if exactly one candidate remains — never guess an ambiguous match.
    if len(rows) == 1:
        lei_v, nm, ctry = _from_gleif_record(rows[0]["attributes"])
        return Entity(lei=lei_v, name=nm, country=ctry, resolution="name")
    return Entity(lei=None, name=name, country=country, resolution="unresolved")


def resolve_entities(specs: list[dict], *, fetcher) -> list[Entity]:
    out: list[Entity] = []
    for spec in specs:
        if spec.get("lei"):
            e = _lookup_lei(spec["lei"], fetcher)
            out.append(e or Entity(None, spec.get("name", ""), spec.get("country", ""),
                                    resolution="unresolved"))
        elif spec.get("isin"):
            out.append(_lookup_isin(spec["isin"], fetcher))
        elif spec.get("name"):
            out.append(_lookup_name(spec["name"], spec.get("country", ""), fetcher))
        else:  # ticker tier — requires OpenFIGI, deferred to Task 1c
            out.append(Entity(None, spec.get("name", ""), spec.get("country", ""),
                              resolution="unresolved"))
    return out
