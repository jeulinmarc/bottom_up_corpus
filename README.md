# bottom_up_corpus

An exhaustive, replicable, **open-data** corpus of **company** primary-source
documents — the *bottom-up / micro* layer that complements
[`cb_corpus`](https://github.com/jeulinmarc/cb_corpus) (the central-bank *macro*
layer). Both feed the same downstream RAG stack (`mvp-graph-rag` / `eigenmind`)
via `RAGDataOrchestrator`.

Every document comes from the issuer's **regulator of record** (SEC, AMF, FCA,
CONSOB, …) — public, primary-source disclosures, with provenance recorded per
file. No proprietary datasets, no machine translation, no model-generated text.

## Three pillars

| Pillar | Region | Source of record | Identity | Status |
|---|---|---|---|---|
| **🇺🇸 SEC** | United States | EDGAR | CIK | complete (reports, ownership, XBRL financials) |
| **🇪🇺 EU** | 14 jurisdictions | national OAMs + Euronext + FCA NSM | LEI / ISIN | 13 backends + cross-market complement |
| **Register** | 8 countries | national business registers | local entity ID | 8 registers merged — [`REGISTER_FINANCIALS.md`](docs/REGISTER_FINANCIALS.md) |

All three pillars share the same discipline (official sources only, stable ids,
exhaustive discovery, never silently partial) and feed the same RAG contract. The
SEC and EU pillars cover listed issuers (CIK / GLEIF LEI); the register pillar
targets the credit and private-company universe of non-listed entities.

### Structured financials

Beyond raw filings, a shared engine (`financials.py`) extracts curated metrics into
a unified per-period row schema (see [`docs/FINANCIALS.md`](docs/FINANCIALS.md)):

| Layer | Universe | Source | Status |
|---|---|---|---|
| **SEC XBRL** | US listed issuers | EDGAR `companyfacts` | ✅ done |
| **EU ESEF / IFRS** (Pillar B) | EU listed issuers | `filings.xbrl.org` json_url + Arelle (Tier B) | ✅ done — [`EU_FINANCIALS.md`](docs/EU_FINANCIALS.md) |
| **Register financials** | Private / credit universe (non-listed) | 8 national registers: 🇳🇴 NO · 🇬🇧 UK · 🇧🇪 BE · 🇱🇺 LU · 🇫🇮 FI · 🇩🇰 DK · 🇪🇪 EE · 🇸🇰 SK | ✅ all merged (#57–66) — [`REGISTER_FINANCIALS.md`](docs/REGISTER_FINANCIALS.md) |

The register pillar targets issuers that never file ESEF — bond obligors, private
companies, bank counterparties. Output lands in `data/financials_register/` (never
merged with `data/financials_eu/`), labelled by `basis` (legal-entity vs.
consolidated). It is governed by a **no-false-data** discipline: any value that
cannot be confirmed from structural anchors is suppressed, not guessed. Leverage
rows carry a `leverage_basis` field (`"borrowings"` or `"total_liabilities"`)
because registers differ — some expose real financial borrowings (BE/LU/SK/DK-ESEF),
others only total liabilities (NO/UK/EE/DK-FSA) — so consumers cannot compare
`debt_to_equity` across registers without knowing the basis.

## Quick start

```bash
pip install -r requirements.txt
python -m pytest -q                       # network-free test suite

export BOTTOM_UP_CORPUS_CONTACT="you@example.com"   # required before any live crawl
```

There is **no default contact** — without it the `User-Agent` carries only the
tool name, so cloning the repo never leaks anyone's address. Regulators ask for a
real contact, so set it before crawling.

```bash
# SEC: build a tiny universe, discover + download
python -m bottom_up_corpus build-universe --tickers AAPL,MSFT --name demo --write
python -m bottom_up_corpus discover --universe demo --download --since 2015-01-01 --write

# EU: acquire one issuer's regulated filings by ISIN (resolves the LEI via GLEIF)
python -c "from bottom_up_corpus.http import Fetcher; from bottom_up_corpus.config import Config; \
from bottom_up_corpus.eu.acquire import acquire; cfg=Config(contact='you@example.com'); \
print(acquire([{'isin':'FR0010193052'}], fetcher=Fetcher(cfg), config=cfg, download=True))"

# Register financials: Norwegian statutory accounts (Brreg, no key required)
python -m bottom_up_corpus register-financials --orgnrs 923609016 --write
# UK Companies House bulk iXBRL (Arelle required; --limit for a bounded test run)
python -m bottom_up_corpus register-financials --ch-bulk accounts_monthly_2024_01.zip --limit 100
```

## Documentation

| Doc | What's inside |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Layer map, data model, corpus lifecycle, issuer-resolution waterfall, design invariants |
| [`docs/SEC_PILLAR.md`](docs/SEC_PILLAR.md) | 🇺🇸 SEC guide: taxonomy, storage layout & naming, the full CLI, identity (rename/merger), ownership & XBRL financials |
| [`docs/EU_PILLAR.md`](docs/EU_PILLAR.md) | 🇪🇺 EU guide: the "European EDGAR" — `OamSource` architecture, identity resolution (LEI/ISIN/OpenFIGI/name), listing dispatch, cross-backend dedup, how to run `acquire` |
| [`docs/EU_BACKENDS.md`](docs/EU_BACKENDS.md) | Per-country backend reference (source API, identity key, doc types, pagination caps) |
| [`docs/FINANCIALS.md`](docs/FINANCIALS.md) | The shared financials engine (reported + derived metrics, ~60 curated concepts) |
| [`docs/EU_FINANCIALS.md`](docs/EU_FINANCIALS.md) | Structured EU ESEF/IFRS financials — json_url stdlib (Tier A) + Arelle (Tier B) |
| [`docs/REGISTER_FINANCIALS.md`](docs/REGISTER_FINANCIALS.md) | Statutory financials from 8 national registers (🇳🇴 NO · 🇬🇧 UK · 🇧🇪 BE · 🇱🇺 LU · 🇫🇮 FI · 🇩🇰 DK · 🇪🇪 EE · 🇸🇰 SK); no-false-data gate; `leverage_basis` field |
| [`docs/INGESTION_RAG.md`](docs/INGESTION_RAG.md) | The RAG ingestion contract + a ready-to-paste orchestrator connector |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Status & next steps |

## Core principles

- **Official primary sources only** — every document comes from the issuer's
  regulator of record; provenance is recorded per filing.
- **Replicability** — stable, date-independent document ids; idempotent,
  convergent crawls; deterministic on-disk layout.
- **Exhaustivity** — discovery via each regulator's own indices/APIs; coverage is
  reconciled against what's expected; incompleteness is **recorded, never silently
  dropped** (a backend that caps a page records a `truncated` error).
- **No-guess identity** — an issuer is bound only on an exact/verified match
  (CIK, LEI, or ISIN); an ambiguous match is left unresolved, never guessed.
- **No-false-data** — in the register-financials pillar, any value that cannot be
  confirmed from structural anchors is suppressed and the reason recorded; a missing
  number beats a wrong one.

## Fair access

The HTTP client sends a declared, contact-carrying `User-Agent` and throttles per
host to stay at/under each regulator's published rate limit (e.g. the SEC's 10
requests/second).
