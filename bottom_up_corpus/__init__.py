"""bottom_up_corpus: an open-data corpus of company filings (SEC EDGAR + ext.).

The bottom-up / micro counterpart to ``cb_corpus`` (central-bank macro layer).
Both feed the same downstream RAG stack via ``RAGDataOrchestrator``.

Public surface mirrors cb_corpus: a filing taxonomy, a canonical record model,
runtime config, and the HTTP fetcher.
"""

from __future__ import annotations

from .completeness import build_matrix
from .config import Config, normalize_cik
from .entity import Entity, EntityRegistry
from .extract import clean_text
from .http import Fetcher
from .models import FilingRecord
from .naming import name_as_of, parse_former_names
from .pipeline import discover_universe, download_universe, render_universe
from .rag import SourceItem, iter_items
from .render import find_chrome, make_chrome_renderer
from .storage import Storage
from .submission import parse_submission, select_primary
from .taxonomy import (
    FULL_SCOPE,
    FormType,
    by_code,
    from_edgar_form,
    parse_scope,
)
from .universe import Issuer, Universe, resolve_ciks, resolve_tickers

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
    "resolve_ciks",
    "discover_universe",
    "download_universe",
    "render_universe",
    "make_chrome_renderer",
    "find_chrome",
    "iter_items",
    "SourceItem",
    "build_matrix",
    "parse_submission",
    "select_primary",
    "clean_text",
    "Entity",
    "EntityRegistry",
    "name_as_of",
    "parse_former_names",
]

__version__ = "0.1.0"
