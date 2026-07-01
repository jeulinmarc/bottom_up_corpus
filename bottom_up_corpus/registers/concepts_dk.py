"""Map Denmark FSA (Erhvervsstyrelsen) DK-GAAP facts to our curated concepts.

Consumes the flat fact map produced by
:func:`bottom_up_corpus.registers.dk_fsa_xbrl.parse_fsa_facts` —
``{"period_end", "currency", "facts": {local_name: float}}`` where each key is
the FSA element local name — and produces the same shape as the BE/FI/NO/UK
siblings: ``{period_end, basis, currency, values, suppressed, unbalanced}`` with
``values[key] = {"value", "unit", "label", "tag"}``. ``basis`` is ``"company"``
(DK-GAAP individual accounts); the currency is **DKK** (never EUR).

GOVERNING PRINCIPLE — NO FALSE DATA. This is open data for the credit universe:
a number we cannot confirm must never be emitted; a *missing* number is strictly
better than a *wrong* one. So every value below is either its confirmed,
directly-tagged figure (or a clean derivation) or it is suppressed with a
recorded reason.

Two DK-GAAP traps drive the gate (ÅRL — the Danish Financial Statements Act):

* **The GrossProfitLoss ≠ revenue trap (§32).** Class-B / micro filers may
  present *Bruttoresultat* (a gross result = revenue − COGS − external costs)
  under ``fsa:GrossProfitLoss`` and **omit** ``fsa:Revenue`` entirely. Mapping
  ``GrossProfitLoss`` to ``revenue`` would badly understate the top line, so we
  **never** do it: ``revenue`` is emitted only from ``fsa:Revenue`` and is
  suppressed when that tag is absent. ``fsa:GrossProfitLoss`` is mapped to its
  own ``gross_profit`` key, correctly labelled.

* **The borrowings-by-instrument trap (leverage).** DK-GAAP class-B does **not**
  tag borrowings by instrument (bank vs bond vs lease), so a debt split by
  instrument can never be fabricated (``financial_debt`` is always suppressed).
  What DK-GAAP *does* provide is a **maturity** split of the non-provision
  liabilities: ``fsa:ShorttermLiabilitiesOtherThanProvisions`` and
  ``fsa:LongtermLiabilitiesOtherThanProvisions`` (the taxonomy names which is
  short vs long — no guessing needed), plus ``fsa:Provisions`` reported
  separately. We emit the maturity buckets as ``short_term_debt`` /
  ``long_term_debt`` **only** when they are directly tagged AND
  ``short + long + provisions`` reconciles to the derived total liabilities
  (i.e. the split fully accounts for the balance sheet); otherwise the split is
  suppressed. Provisions stay a separate reported value and are **never** folded
  into the debt keys.

The confidence gate (§4 of the design doc):

* **Primary balance:** ``Assets == LiabilitiesAndEquity`` within tolerance.
  Mismatch → the whole filing is untrustworthy: ``unbalanced=True``, no values
  (a wrong balance sheet is worse than none). When either anchor is absent the
  gate cannot run; we proceed (directly-tagged values still stand), mirroring
  the BE/FI/UK siblings.
* **Total liabilities** are *derived* as ``LiabilitiesAndEquity − Equity`` — this
  captures provisions automatically (provisions are part of the passiv total but
  are excluded from ``LiabilitiesOtherThanProvisions``), so no separate
  provisions gate is needed.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# The curated concept pack: curated key -> (FSA local name, human label). Each
# entry is a directly-tagged figure; emitted when present, suppressed (with a
# recorded reason) when absent. ``liabilities`` is derived (below) and
# ``revenue`` / the maturity split carry extra NO-FALSE-DATA logic.
# ---------------------------------------------------------------------------
DK_PACK: dict[str, tuple[str, str]] = {
    "assets":         ("Assets", "Total assets"),
    "assets_current": ("CurrentAssets", "Current assets"),
    "cash":           ("CashAndCashEquivalents", "Cash and cash equivalents"),
    "equity":         ("Equity", "Equity"),
    "net_income":     ("ProfitLoss", "Profit/loss for the year"),
    "gross_profit":   ("GrossProfitLoss", "Gross profit/loss (Bruttoresultat)"),
}

# FSA local names used by the derivations / gate (not in the plain pack loop).
_ASSETS = "Assets"
_LIAB_AND_EQUITY = "LiabilitiesAndEquity"
_EQUITY = "Equity"
_REVENUE = "Revenue"
_PROVISIONS = "Provisions"
_SHORT = "ShorttermLiabilitiesOtherThanProvisions"
_LONG = "LongtermLiabilitiesOtherThanProvisions"


def _tol(scale: float) -> float:
    """Absolute tolerance for a balance identity at magnitude ``scale``:
    ``max(2, 0.005 * |scale|)`` — 0.5%, but never tighter than 2 DKK (so tiny
    micro-entity filings are not tripped by rounding)."""
    return max(2.0, 0.005 * abs(scale))


def map_fsa_facts(parsed: dict) -> dict:
    """One ``parse_fsa_facts`` result -> curated DK-GAAP financials.

    ``parsed`` is ``{"period_end", "currency", "facts": {local_name: float}}``.
    Returns ``{period_end, basis, currency, values, suppressed, unbalanced}``:
    ``basis`` is ``"company"``; ``values[key] = {"value", "unit":"DKK", "label",
    "tag":"fsa:<Name>"}``; ``suppressed`` is a list of ``(key, reason)``;
    ``unbalanced`` is True when the primary balance gate fails (then ``values``
    is empty).
    """
    facts: dict[str, float] = parsed.get("facts") or {}
    period_end = parsed.get("period_end")
    currency = parsed.get("currency") or "DKK"

    values: dict[str, dict] = {}
    suppressed: list[tuple[str, str]] = []

    def emit(key: str, value: float, tag: str, label: str) -> None:
        values[key] = {"value": value, "unit": "DKK", "label": label, "tag": tag}

    def suppress(key: str, reason: str) -> None:
        suppressed.append((key, reason))

    def result(unbalanced: bool) -> dict:
        return {
            "period_end": period_end, "basis": "company", "currency": currency,
            "values": values, "suppressed": suppressed, "unbalanced": unbalanced,
        }

    assets = facts.get(_ASSETS)
    liab_and_equity = facts.get(_LIAB_AND_EQUITY)
    equity = facts.get(_EQUITY)

    # --- Primary balance gate: Assets == LiabilitiesAndEquity. On mismatch the
    #     whole filing is untrustworthy -> unbalanced, emit NO values. When
    #     either anchor is absent the gate cannot run; we proceed (directly-tagged
    #     values still stand), mirroring the BE/FI/UK siblings.
    if assets is not None and liab_and_equity is not None:
        scale = max(abs(assets), abs(liab_and_equity))
        if abs(assets - liab_and_equity) > _tol(scale):
            suppress("__all__",
                     f"unbalanced: Assets {assets} != LiabilitiesAndEquity "
                     f"{liab_and_equity} beyond tol")
            return result(unbalanced=True)

    # --- Plain confirmed pack: directly-tagged value or a recorded absence.
    for key, (local, label) in DK_PACK.items():
        raw = facts.get(local)
        if raw is None:
            suppress(key, f"fsa:{local} absent on the filing")
        else:
            emit(key, raw, f"fsa:{local}", label)

    # --- Total liabilities, DERIVED as LiabilitiesAndEquity − Equity. This
    #     captures provisions automatically (they are part of the passiv total).
    liabilities: float | None = None
    if liab_and_equity is not None and equity is not None:
        liabilities = liab_and_equity - equity
        emit("liabilities", liabilities,
             "fsa:LiabilitiesAndEquity − fsa:Equity (derived)",
             "Total liabilities (derived)")
    else:
        suppress("liabilities",
                 "cannot derive: fsa:LiabilitiesAndEquity or fsa:Equity absent")

    # --- Provisions: a real directly-tagged passiv total, reported separately
    #     (never counted as debt).
    provisions = facts.get(_PROVISIONS)
    if provisions is not None:
        emit("provisions", provisions, "fsa:Provisions", "Provisions")

    # --- Revenue (§32 trap): emit ONLY from fsa:Revenue. When it is absent the
    #     filer is presenting Bruttoresultat instead; GrossProfitLoss is NOT
    #     revenue, so we suppress rather than substitute it.
    revenue = facts.get(_REVENUE)
    if revenue is not None:
        emit("revenue", revenue, "fsa:Revenue", "Revenue")
    else:
        suppress("revenue",
                 "fsa:Revenue absent (ÅRL §32: filer presents Bruttoresultat; "
                 "fsa:GrossProfitLoss is NOT revenue — never substituted)")

    # --- Leverage: maturity split (NO FALSE DATA). Emit short_term_debt /
    #     long_term_debt only when the taxonomy-labelled buckets are present AND
    #     short + long + provisions reconciles to the derived total liabilities
    #     (the split fully accounts for the passiv). Otherwise suppress the split
    #     — never map a lone total as long_term_debt, never guess a maturity.
    short = facts.get(_SHORT)
    long = facts.get(_LONG)
    _emit_maturity_split(emit, suppress, liabilities, short, long, provisions)

    # --- Borrowings-by-instrument is never available for class-B filers; a debt
    #     split by instrument is never fabricated (liabilities-based leverage only).
    suppress("financial_debt",
             "DK-GAAP class-B does not tag borrowings by instrument — no "
             "bank/bond/lease split; never fabricated (no false data)")

    return result(unbalanced=False)


def _emit_maturity_split(emit, suppress, liabilities, short, long, provisions):
    """Emit ``short_term_debt`` / ``long_term_debt`` from the DK-GAAP maturity
    buckets, or suppress the split atomically with a recorded reason.

    The buckets are ``fsa:ShorttermLiabilitiesOtherThanProvisions`` (short) and
    ``fsa:LongtermLiabilitiesOtherThanProvisions`` (long) — the taxonomy labels
    which is which, so no guessing is involved. They are trusted only when they
    reconcile: ``short + long + provisions`` must equal the derived total
    liabilities within tolerance, i.e. the maturity split (plus provisions) fully
    accounts for the passiv. On reconciliation each *present* bucket is emitted
    with its confirmed label; a bucket the filing did not tag is left absent
    (never synthesised — provisions/equity make a 0 long-term genuinely unknown
    as a *reported* line). Provisions stay separate and are never added to debt.
    """
    keys = ("short_term_debt", "long_term_debt")

    def suppress_both(reason: str) -> None:
        for key in keys:
            suppress(key, reason)

    if liabilities is None:
        suppress_both("no derived total liabilities to reconcile the maturity "
                      "split against — split suppressed")
        return
    if short is None and long is None:
        suppress_both("no maturity buckets tagged "
                      "(fsa:Shortterm/LongtermLiabilitiesOtherThanProvisions) — "
                      "no split to emit")
        return

    recon = (short or 0.0) + (long or 0.0) + (provisions or 0.0)
    if abs(recon - liabilities) > _tol(abs(liabilities)):
        suppress_both(
            f"maturity split does not reconcile: short {short or 0.0} + long "
            f"{long or 0.0} + provisions {provisions or 0.0} = {recon} != "
            f"derived liabilities {liabilities} beyond tol — suppressed "
            "(never map a lone total as long_term_debt)")
        return

    # Reconciled: emit each bucket the taxonomy actually tagged.
    if short is not None:
        emit("short_term_debt", short,
             "fsa:ShorttermLiabilitiesOtherThanProvisions",
             "Short-term liabilities other than provisions")
    else:
        suppress("short_term_debt",
                 "no fsa:ShorttermLiabilitiesOtherThanProvisions on the filing")
    if long is not None:
        emit("long_term_debt", long,
             "fsa:LongtermLiabilitiesOtherThanProvisions",
             "Long-term liabilities other than provisions")
    else:
        suppress("long_term_debt",
                 "no fsa:LongtermLiabilitiesOtherThanProvisions on the filing "
                 "(short bucket reconciles alone — long-term total not tagged, "
                 "never synthesised)")
