# Examples

One runnable script per file, each showing a single piece of `bottom_up_corpus`.
Run any of them from the repo root, e.g.:

```bash
# Set a contact so the SEC User-Agent carries one (fair-access politeness).
export BOTTOM_UP_CORPUS_CONTACT="you@example.com"

./venv/bin/python examples/01_resolve_universe.py
```

Scripts that hit SEC EDGAR are bounded (one issuer, a filing or two) and write any
artifacts to a temporary directory, so they leave the repo's `data/` untouched.

| Script | Shows |
|---|---|
| `01_resolve_universe.py` | Resolve tickers → CIKs via the official SEC ticker map |
| `02_discover_filings.py` | List an issuer's family-A filings (metadata only) |
| `03_download_and_extract.py` | Download one filing and extract clean RAG-ready text |
| `04_xbrl_financials.py` | Pull XBRL facts → a period summary + derived metrics |
| `05_rag_items.py` | Build a tiny corpus and iterate the `SourceItem`s the RAG ingests |
