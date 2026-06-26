"""Bounded European acquisition demo (FR/DE/IT/ES). Network; set BOTTOM_UP_CORPUS_CONTACT.

    ./venv/bin/python examples/16_eu_acquire.py
"""
from bottom_up_corpus import Config, Fetcher
from bottom_up_corpus.eu.acquire import acquire

SEED = [  # Increment A: FR (API) + ES (scrape)
    {"name": "TotalEnergies SE", "country": "FR"},
    {"name": "LVMH Moet Hennessy Louis Vuitton SE", "country": "FR"},
    {"name": "Iberdrola SA", "country": "ES"},
    {"name": "Banco Santander SA", "country": "ES"},
]

cfg = Config()
summary = acquire(SEED, fetcher=Fetcher(cfg), config=cfg, download=True)
print(summary)
