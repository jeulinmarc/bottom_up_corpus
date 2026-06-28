from datetime import date
from bottom_up_corpus.eu.documents import Document, DOC_FAMILY
from bottom_up_corpus.eu.dispatcher import merge_documents


def _doc(source, sha):
    return Document(doc_id=f"{source}-1", lei="L1", country="DE", doc_type="annual_report",
                    period_end=date(2023, 12, 31), published_ts="2024-03-01", discovered_ts="x",
                    language="de", source=source, files=[{"name": "r.html", "sha256": sha}],
                    native_meta={})


def test_doc_family_maps_types():
    assert DOC_FAMILY["annual_report"] == "ESEF-AR"
    assert DOC_FAMILY["half_year_report"] == "HY"


def test_merge_dedupes_same_document_across_backends():
    a = _doc("oam-de", "abc")
    b = _doc("filings.xbrl.org", "abc")  # same (lei, type, period, file-hash) -> dup
    c = _doc("oam-de", "different")
    merged = merge_documents([[a, c], [b]])
    assert len(merged) == 2  # a/b collapsed, c kept


def _titled(source, title, *, day="2024-03-01", doc_type="other", fname="f.pdf"):
    return Document(doc_id=f"{source}-{fname}", lei="L1", country="PT", doc_type=doc_type,
                    period_end=None, published_ts=day, discovered_ts="x", language="en",
                    source=source, files=[{"name": fname}], native_meta={"title": title})


def test_content_key_dedupes_across_backends_despite_different_filenames():
    """Same (lei, day, title) collapses even when file names/types differ —
    the national doc (first) wins; the Euronext complement is dropped."""
    nat = _titled("oam-nl", "FY2024 Results", doc_type="annual_report", fname="afm-r.zip")
    eur = _titled("euronext", "FY2024 Results", doc_type="other", fname="euronext-9.pdf")
    merged = merge_documents([[nat], [eur]])
    assert len(merged) == 1 and merged[0].source == "oam-nl"


def test_different_titles_same_day_both_kept():
    a = _titled("euronext", "Dividend announcement", fname="euronext-1.pdf")
    b = _titled("euronext", "Notice of General Meeting", fname="euronext-2.pdf")
    assert len(merge_documents([[a, b]])) == 2


def test_titleless_documents_are_not_content_merged():
    """Two title-less docs with distinct files must both survive (no over-merge)."""
    a = Document(doc_id="x", lei="L1", country="PT", doc_type="other", period_end=None,
                 published_ts="2024-03-01", discovered_ts="x", language="en", source="euronext",
                 files=[{"name": "a.pdf"}], native_meta={})
    b = Document(doc_id="y", lei="L1", country="PT", doc_type="other", period_end=None,
                 published_ts="2024-03-01", discovered_ts="x", language="en", source="euronext",
                 files=[{"name": "b.pdf"}], native_meta={})
    assert len(merge_documents([[a, b]])) == 2
