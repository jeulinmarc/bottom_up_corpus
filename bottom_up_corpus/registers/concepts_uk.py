"""Map UK Companies House (FRC-taxonomy) iXBRL facts to our curated concepts.

Consumes a ``flatten_oim_json`` flatten — ``{local_name: [point, ...]}``, each
point ``{"val","end","unit","tag",...}`` — keyed by the concept's **local name**
(the FRC namespace year varies across filings, so we never key on the full QName).
Produces the same ``{period_end, basis, currency, values}`` shape as the NO
sibling (:func:`bottom_up_corpus.registers.concepts_no.map_brreg_entry`), plus a
``suppressed`` audit trail and an ``unbalanced`` flag.

GOVERNING PRINCIPLE — NO FALSE DATA. This corpus is open data for hedge funds: a
number we know is wrong must never be emitted; a *missing* number is strictly
better than a *wrong* one. So we prefer directly-tagged values, derive only from
structural anchors, and emit a derived value only when its inputs are present AND
a confidence gate passes — otherwise we suppress it and record the reason. We
never default a missing balance-sheet item to zero.

The confidence gate (independently-tagged anchors only):
- **Primary:** ``NetAssetsLiabilities == Equity`` within tolerance. If they
  disagree the whole filing is untrustworthy -> ``unbalanced=True``, no values.
- **Anchor:** when ``FixedAssets`` is tagged, verify
  ``TotalAssetsLessCurrentLiabilities == FixedAssets + NetCurrentAssets``; on
  mismatch the inputs are proven inconsistent, so EVERY derived balance item
  (assets, liabilities, liabilities_current, short/long-term debt) is suppressed
  — the P&L and directly-tagged equity/net_assets/cash still stand.
- **Completeness:** the derived liability/debt block is emitted atomically, only
  when BOTH the current (``CA − NCA``) and long-term (``TALCL − NetAssets``)
  halves are derivable; if only one is, the whole block is withheld so the
  engine's ``total_debt`` is never silently understated.

``assets`` is derived from ``TotalAssetsLessCurrentLiabilities + current
liabilities`` (a robust structural anchor) rather than ``FixedAssets +
CurrentAssets`` — ``FixedAssets`` is frequently *untagged* (dimensioned away),
which would silently UNDERSTATE assets = a false number.
"""
from __future__ import annotations

# curated key -> FRC local-name fallbacks, highest priority first. First present
# (in the current period) wins. NetAssetsLiabilities is handled separately (it is
# both the gate anchor and its own ``net_assets`` output key).
UK_FIELDS: dict[str, tuple[str, ...]] = {
    "revenue": ("TurnoverRevenue",),
    "gross_profit": ("GrossProfitLoss",),
    "operating_income": ("OperatingProfitLoss",),
    "pretax_income": ("ProfitLossOnOrdinaryActivitiesBeforeTax",),
    "income_tax": ("TaxTaxCreditOnProfitOrLossOnOrdinaryActivities",),
    "net_income": ("ProfitLoss",),
    "equity": ("Equity",),
    "assets_current": ("CurrentAssets",),
    "cash": ("CashBankOnHand",),
    "receivables": ("Debtors",),
    "inventory": ("TotalInventories",),
}


def _tol(scale: float) -> float:
    """Absolute tolerance for a balance identity at magnitude ``scale``.

    ``max(2, 0.005 * |scale|)`` — 0.5% of the figure, but never tighter than 2
    currency units (so tiny micro-entity filings are not tripped by rounding).
    """
    return max(2.0, 0.005 * abs(scale))


def _current_period(flat: dict[str, list[dict]]) -> tuple[str, dict, str] | None:
    """(period_end, T, currency) for the latest period present, or None.

    ``T`` maps each concept's local name -> its value at ``period_end`` (the
    tagged figures; prior-year comparatives are ignored). ``currency`` is the
    first non-empty unit seen at ``period_end`` (else GBP)."""
    ends = [p["end"] for pts in flat.values() for p in pts if p.get("end")]
    if not ends:
        return None
    pe = max(ends)
    tagged: dict[str, float] = {}
    currency = ""
    for local, pts in flat.items():
        for p in pts:
            if p.get("end") != pe:
                continue
            tagged[local] = p["val"]
            if not currency and p.get("unit"):
                currency = p["unit"]
            break            # one value per concept per period (json_url has no dupes)
    if not tagged:
        return None
    return pe, tagged, (currency or "GBP")


def map_ch_facts(flat: dict[str, list[dict]]) -> dict | None:
    """One Companies House OIM flatten -> curated financials for the current period.

    Returns ``{period_end, basis, currency, values, suppressed, unbalanced}`` or
    ``None`` when there is no usable current period at all. ``values[key]`` =
    ``{"value","unit","label","tag"}`` (derived values carry a ``"…(derived)"``
    tag). ``suppressed`` is a list of ``(key, reason)`` for every balance-sheet
    figure we declined to emit; ``unbalanced`` is True when NetAssets != Equity.
    """
    cp = _current_period(flat)
    if cp is None:
        return None
    pe, T, currency = cp

    values: dict[str, dict] = {}
    suppressed: list[tuple[str, str]] = []

    def emit(key: str, value, tag: str) -> None:
        values[key] = {"value": value, "unit": currency, "label": key, "tag": tag}

    # 2. Direct map: prefer directly-tagged values (first present fallback wins).
    for key, names in UK_FIELDS.items():
        for name in names:
            if name in T:
                emit(key, T[name], name)
                break
    if "NetAssetsLiabilities" in T:                       # gate anchor + output key
        emit("net_assets", T["NetAssetsLiabilities"], "NetAssetsLiabilities")

    # 3. Confidence gate — independently-tagged structural anchors only.
    E = T.get("Equity")
    NA = T.get("NetAssetsLiabilities")
    FA = T.get("FixedAssets")
    CA = T.get("CurrentAssets")
    NCA = T.get("NetCurrentAssetsLiabilities")
    TALCL = T.get("TotalAssetsLessCurrentLiabilities")

    # Primary: NetAssets must equal Equity. If not, the filing is not trustworthy
    # -> unbalanced, emit NO values (a wrong balance sheet is worse than none).
    if E is not None and NA is not None and abs(NA - E) > _tol(max(abs(NA), abs(E))):
        return {
            "period_end": pe, "basis": "company", "currency": currency,
            "values": {},
            "suppressed": [("__all__", f"unbalanced: NetAssets {NA} != Equity {E}")],
            "unbalanced": True,
        }

    # Anchor: when FixedAssets is tagged, TALCL must reconcile to FA + NCA. On
    # mismatch the balance-sheet inputs are proven inconsistent, so EVERY derived
    # balance item is suppressed below (recorded per-key), keeping only the P&L
    # and directly-tagged equity / net_assets / cash.
    suppress_balance = False
    if FA is not None and TALCL is not None and NCA is not None:
        if abs(TALCL - (FA + NCA)) > _tol(TALCL):
            suppress_balance = True

    NAeff = NA if NA is not None else E       # Primary guarantees NA == E when both present

    # 4. Derivations. The derived liability/debt block is emitted ATOMICALLY —
    # all five items or none — and only when NEITHER integrity concern applies:
    #   (a) suppress_balance — the FixedAssets reconciliation failed, so the
    #       inputs are proven inconsistent (a bad NetCurrentAssets would poison
    #       liabilities_current / total_debt / current_ratio downstream); and
    #   (b) the liability picture is incomplete — the engine computes
    #       total_debt = long_term_debt + short_term_debt, so emitting only one
    #       half would silently UNDERSTATE liabilities (a wrong number). Emit the
    #       block only when BOTH halves are derivable.
    DERIVED_BALANCE = ("liabilities_current", "short_term_debt", "long_term_debt",
                       "assets", "liabilities")
    liabilities_current = (CA - NCA) if (CA is not None and NCA is not None) else None
    long_term_debt = (TALCL - NAeff) if (TALCL is not None and NAeff is not None) else None

    if suppress_balance:
        for key in DERIVED_BALANCE:
            suppressed.append((key, "gate: TALCL != FixedAssets + NetCurrentAssets"))
    elif liabilities_current is None or long_term_debt is None:
        missing = []
        if liabilities_current is None:
            missing.append("CurrentAssets/NetCurrentAssetsLiabilities")
        if long_term_debt is None:
            missing.append("TotalAssetsLessCurrentLiabilities/NetAssets")
        reason = "incomplete liabilities (total_debt would understate): missing " + " + ".join(missing)
        for key in DERIVED_BALANCE:
            suppressed.append((key, reason))
    else:
        lc_tag = "CurrentAssets − NetCurrentAssetsLiabilities (derived)"
        emit("liabilities_current", liabilities_current, lc_tag)
        # short_term_debt mirrors current liabilities so the engine's total_debt
        # (= long_term_debt + short_term_debt) reconstructs TOTAL liabilities —
        # the liabilities-basis leverage, consistent with the NO register.
        emit("short_term_debt", liabilities_current, lc_tag)
        emit("long_term_debt", long_term_debt,
             "TotalAssetsLessCurrentLiabilities − NetAssets (derived)")
        # assets = TALCL + current liabilities (robust; NEVER FixedAssets +
        # CurrentAssets, which understates when FixedAssets is untagged).
        emit("assets", TALCL + liabilities_current,
             "TotalAssetsLessCurrentLiabilities + current liabilities (derived)")
        emit("liabilities", liabilities_current + long_term_debt,
             "current + long-term liabilities (derived)")

    return {
        "period_end": pe, "basis": "company", "currency": currency,
        "values": values, "suppressed": suppressed, "unbalanced": False,
    }
