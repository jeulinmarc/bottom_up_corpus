"""Pull XBRL company facts and build a per-period financial summary.

Each summary carries the reported line items plus a block of derived metrics
(total debt, EBITDA, leverage ratios, …), in the issuer's reporting currency.
Run:

    ./venv/bin/python examples/04_xbrl_financials.py
"""
from __future__ import annotations

from bottom_up_corpus import Config, Fetcher
from bottom_up_corpus.sources.edgar_xbrl import EdgarXBRL

APPLE_CIK = "320193"

cfg = Config()
fetcher = Fetcher(cfg)

facts, summaries = EdgarXBRL(fetcher, cfg).period_summaries(APPLE_CIK, since_year=2022)

# Most recent annual period.
annual = [s for s in summaries if s.frequency == "annual"]
latest = max(annual, key=lambda s: s.period_end)
print(f"{latest.company} — FY{latest.fy} (ended {latest.period_end}), "
      f"published {latest.publication_date}, currency {latest.currency}")


def show(label: str, source: dict, key: str) -> None:
    row = source.get(key)
    if row:
        print(f"  {label:24} {row['value']:>20,.0f}  {row['unit']}")


print(" reported:")
show("Revenue", latest.values, "revenue")
show("Net income", latest.values, "net_income")
show("Total assets", latest.values, "assets")
print(" derived:")
show("Total debt", latest.derived, "total_debt")
show("EBITDA", latest.derived, "ebitda")
nde = latest.derived.get("net_debt_to_ebitda")
if nde:
    print(f"  {'Net debt / EBITDA':24} {nde['value']:>20.2f}  {nde['unit']}")
