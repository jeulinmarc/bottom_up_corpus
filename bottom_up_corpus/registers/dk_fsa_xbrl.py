"""Denmark FSA (Erhvervsstyrelsen) DK-GAAP XBRL parser — stdlib only, no Arelle.

Parses a DK-GAAP FSA bare XBRL instance document (as filed with Virk/ERST)
into a flat dict of monetary facts keyed by local name.

Namespace contract
------------------
The DK-GAAP taxonomy namespaces are matched **by URI**, never by prefix, using
Clark notation (``elem.tag == "{uri}LocalName"``).  Prefixes in real filings
may differ from the canonical fsa/gsd/cmn mapping used in the taxonomy docs.

    FSA facts    : http://xbrl.dcca.dk/fsa
    GSD metadata : http://xbrl.dcca.dk/gsd
    CMN commons  : http://xbrl.dcca.dk/cmn

Context selection
-----------------
Private (class-B) DK filings carry no ConsolidatedSoloDimension — their
contexts have no scenario at all.  The selection priority is:

  1. **No-dimension** contexts (no ``xbrli:scenario`` child, or an empty one)
  2. Fall back to ``cmn:ConsolidatedSoloDimension = SoloMember``
  3. Fall back to ``ConsolidatedMember``

Mixed-basis facts (consolidated + solo) are never emitted together.

Duplicate-period deduplication
-------------------------------
Balance-sheet items appear for both the current and prior year instant dates.
When a local name appears in multiple selected contexts the fact whose context
carries the **latest** date (``xbrli:instant`` or ``xbrli:endDate``) is kept.

Public API
----------
parse_fsa_facts(source) -> {"period_end": str|None, "currency": str,
                             "facts": {local_name: float}}
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from typing import Union

# ── XBRL core namespace URIs ──────────────────────────────────────────────────
_NS_XBRLI = "http://www.xbrl.org/2003/instance"
_NS_XBRLDI = "http://xbrl.org/2006/xbrldi"
_NS_XSI = "http://www.w3.org/2001/XMLSchema-instance"

# ── DK-GAAP taxonomy namespace URIs ──────────────────────────────────────────
_NS_FSA = "http://xbrl.dcca.dk/fsa"

# ── Clark-notation tags ───────────────────────────────────────────────────────
_TAG_CONTEXT = f"{{{_NS_XBRLI}}}context"
_TAG_PERIOD = f"{{{_NS_XBRLI}}}period"
_TAG_END_DATE = f"{{{_NS_XBRLI}}}endDate"
_TAG_INSTANT = f"{{{_NS_XBRLI}}}instant"
_TAG_SCENARIO = f"{{{_NS_XBRLI}}}scenario"
_TAG_EXPLICIT = f"{{{_NS_XBRLDI}}}explicitMember"
_TAG_UNIT = f"{{{_NS_XBRLI}}}unit"
_TAG_MEASURE = f"{{{_NS_XBRLI}}}measure"

_ATTR_NIL = f"{{{_NS_XSI}}}nil"

# Local name of the DK solo/consolidated dimension (any namespace prefix)
_DIM_SOLO_LOCAL = "ConsolidatedSoloDimension"
_MEM_SOLO = "SoloMember"
_MEM_CONSOLIDATED = "ConsolidatedMember"

# XBRL boilerplate element names — never data facts
_SKIP_LOCAL = frozenset({"context", "unit", "schemaRef"})

# FSA namespace prefix for Clark matching
_FSA_PREFIX = f"{{{_NS_FSA}}}"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _local(tag: str) -> str:
    """Return the local name from a Clark-notation tag ``{uri}local``."""
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag.split(":", 1)[-1]


def _parse_date(text: str) -> "date | None":
    """Parse ``YYYY-MM-DD`` (possibly with trailing timestamp) to a date."""
    try:
        return date.fromisoformat(text.strip()[:10])
    except (ValueError, AttributeError):
        return None


def _context_date(ctx: ET.Element) -> "date | None":
    """Return the most specific period date for a context element.

    Prefers ``xbrli:instant``; falls back to ``xbrli:endDate``.
    Returns ``None`` when neither is present or parseable.
    """
    period = ctx.find(_TAG_PERIOD)
    if period is None:
        return None
    for tag in (_TAG_INSTANT, _TAG_END_DATE):
        el = period.find(tag)
        if el is not None and el.text and el.text.strip():
            d = _parse_date(el.text)
            if d is not None:
                return d
    return None


def _classify_context(ctx: ET.Element) -> "str | None":
    """Classify a context as 'nodim', 'solo', 'consolidated', or None.

    'None' means the context has dimensional members that are not the
    ConsolidatedSoloDimension — these contexts are ignored.
    """
    scenario = ctx.find(_TAG_SCENARIO)
    if scenario is None or len(list(scenario)) == 0:
        return "nodim"

    # Scan for an explicit ConsolidatedSoloDimension member
    for member in scenario.iter(_TAG_EXPLICIT):
        dim_qname = member.get("dimension", "")
        dim_local = dim_qname.split(":")[-1]
        if dim_local == _DIM_SOLO_LOCAL:
            mem_text = (member.text or "").strip()
            mem_local = mem_text.split(":")[-1]
            if mem_local == _MEM_SOLO:
                return "solo"
            if mem_local == _MEM_CONSOLIDATED:
                return "consolidated"

    # Some other dimension (e.g. typed member for board members) — skip
    return None


def _build_selected_contexts(root: ET.Element) -> "dict[str, date | None]":
    """Return ``{ctx_id: period_date}`` for the selected basis.

    Priority: nodim > solo > consolidated.  Never mixes bases.
    """
    nodim: dict[str, "date | None"] = {}
    solo: dict[str, "date | None"] = {}
    consolidated: dict[str, "date | None"] = {}

    for ctx in root.iter(_TAG_CONTEXT):
        ctx_id = ctx.get("id", "")
        classification = _classify_context(ctx)
        d = _context_date(ctx)
        if classification == "nodim":
            nodim[ctx_id] = d
        elif classification == "solo":
            solo[ctx_id] = d
        elif classification == "consolidated":
            consolidated[ctx_id] = d

    if nodim:
        return nodim
    if solo:
        return solo
    return consolidated


def _detect_currency(root: ET.Element) -> str:
    """Return the ISO-4217 currency code from the first ``xbrli:unit``.

    Defaults to ``"DKK"`` when no ``iso4217:`` measure is found.
    """
    for unit in root.iter(_TAG_UNIT):
        measure = unit.find(_TAG_MEASURE)
        if measure is not None and measure.text:
            text = measure.text.strip()
            if text.startswith("iso4217:"):
                return text.split(":", 1)[1]
    return "DKK"


# ── Public API ────────────────────────────────────────────────────────────────

def parse_fsa_facts(
    source: Union[str, bytes, Path],
) -> dict:
    """Parse a DK-GAAP FSA XBRL instance into flat monetary facts.

    Parameters
    ----------
    source:
        A file path (``str`` or ``pathlib.Path``) or raw bytes of the XBRL
        instance document.

    Returns
    -------
    dict with keys:
        ``"period_end"``  — ``"YYYY-MM-DD"`` of the max selected-context date,
                            or ``None`` when no dated context is found.
        ``"currency"``    — ISO-4217 code (``"DKK"`` unless the unit says otherwise).
        ``"facts"``       — ``{local_name: float}`` for every numeric fact in
                            the ``fsa`` namespace whose ``contextRef`` is a
                            selected context.  When the same local name appears
                            in multiple selected contexts the fact with the
                            **latest** context date is kept (current-period
                            precedence for balance-sheet instant facts).
    """
    if isinstance(source, (str, Path)):
        tree = ET.parse(str(source))
        root = tree.getroot()
    else:
        root = ET.fromstring(source)

    # Build {ctx_id: date | None} for the selected basis
    selected = _build_selected_contexts(root)

    # period_end = max date among selected contexts
    all_dates = [d for d in selected.values() if d is not None]
    period_end: "str | None" = max(all_dates).isoformat() if all_dates else None

    # Currency
    currency = _detect_currency(root)

    # Collect fsa-namespace numeric facts, current-period wins on ties
    # {local_name: (value, context_date)}
    fact_best: dict[str, tuple[float, "date | None"]] = {}

    for elem in root:
        # Match by namespace URI via Clark prefix — never by prefix name
        if not elem.tag.startswith(_FSA_PREFIX):
            continue

        local = elem.tag[len(_FSA_PREFIX):]  # equivalent to _local() but faster
        if local in _SKIP_LOCAL:
            continue

        ctx_ref = elem.get("contextRef")
        if ctx_ref not in selected:
            continue

        # Skip xsi:nil facts
        if elem.get(_ATTR_NIL, "").lower() == "true":
            continue

        # Must be numeric
        text = (elem.text or "").strip()
        if not text:
            continue
        try:
            value = float(text)
        except ValueError:
            continue  # text/boolean fact — skip

        ctx_date = selected[ctx_ref]

        # Current-period selection: keep the fact with the max context date.
        # None dates lose to any real date; equal dates keep the first seen.
        if local not in fact_best:
            fact_best[local] = (value, ctx_date)
        else:
            existing_date = fact_best[local][1]
            newer = (
                ctx_date is not None
                and (existing_date is None or ctx_date > existing_date)
            )
            if newer:
                fact_best[local] = (value, ctx_date)

    facts = {name: val for name, (val, _) in fact_best.items()}

    return {"period_end": period_end, "currency": currency, "facts": facts}
