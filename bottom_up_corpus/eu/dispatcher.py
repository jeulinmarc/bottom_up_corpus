from __future__ import annotations
from .documents import Document


def merge_documents(per_backend: list[list[Document]]) -> list[Document]:
    """Flatten and dedupe documents discovered by multiple backends.

    Two complementary dedup keys, first-occurrence wins (backend order =
    caller's priority, so the more-complete national document beats a
    complement like Euronext on overlap):

    * **file key** ``(lei, doc_type, period_end, file hashes/names)`` — the
      original; collapses the literally-same artefact.
    * **content key** ``(lei, day, normalized title)`` — collapses the same
      announcement reported by two backends even when their file names differ.
      Skipped for documents with no title or no day, so title-less documents
      are never merged this way (no silent loss of distinct documents).
    """
    seen_file: dict[tuple, Document] = {}
    seen_content: set[tuple] = set()
    out: list[Document] = []
    for docs in per_backend:
        for d in docs:
            fk = d.key()
            if fk in seen_file:
                continue
            ck = d.content_key()
            if ck is not None and ck in seen_content:
                continue
            seen_file[fk] = d
            if ck is not None:
                seen_content.add(ck)
            out.append(d)
    return out
