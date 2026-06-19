"""bottom_up_corpus: an open-data corpus of company filings (SEC EDGAR + ext.).

The bottom-up / micro counterpart to ``cb_corpus`` (central-bank macro layer).
Both feed the same downstream RAG stack via ``RAGDataOrchestrator``.

Public surface mirrors cb_corpus: a filing taxonomy, a canonical record model,
runtime config, and the HTTP fetcher.
"""

from __future__ import annotations

from .completeness import build_matrix
from .config import Config, normalize_cik
from .http import Fetcher
from .models import FilingRecord
from .pipeline import discover_universe
from .storage import Storage
from .taxonomy import (
    FULL_SCOPE,
    FormType,
    by_code,
    from_edgar_form,
    parse_scope,
)
from .universe import Issuer, Universe, resolve_tickers

__all__ = [
    "Config",
    "normalize_cik",
    "Fetcher",
    "FilingRecord",
    "FormType",
    "FULL_SCOPE",
    "by_code",
    "from_edgar_form",
    "parse_scope",
    "Storage",
    "Universe",
    "Issuer",
    "resolve_tickers",
    "discover_universe",
    "build_matrix",
]

__version__ = "0.1.0"
