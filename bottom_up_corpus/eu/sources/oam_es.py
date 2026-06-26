"""CNMV (www.cnmv.es) backend — Spain.

The CNMV is an ASP.NET WebForms site. There is no JSON API; the flow is:

1. **Resolve name → NIF** (Spanish tax id):
   - GET the BusquedaPorEntidad landing to scrape the three WebForms hidden
     fields (__VIEWSTATE, __VIEWSTATEGENERATOR, __EVENTVALIDATION).
   - Form-POST (urlencoded) back to the same host with those fields plus the
     search text, receiving an HTML page that contains a ``<select>``
     (``lstSeleccion``) whose ``<option value="NIF">NAME</option>`` entries are
     the candidate issuers.
   - Pick the option whose normalised name EXACTLY matches the target.
     STRICT: if no exact match or more than one exact match → ``_record_error``
     and return None. Never use prefix / substring matching.

2. **List documents** — for each NIF-keyed register page, parse ``<a …
   id="…subtituloRegistroEnlace" href="https://www.cnmv.es/webservices/
   verdocumento/ver?t={GUID}">`` rows. The verdocumento href is absolute,
   stable, and re-fetchable (no session binding). Paginate via ``&page=N``
   up to ``_MAX_PAGES``; if the last page is still full, record a
   ``truncated`` error (never silently partial — mirrors oam_it.py).

3. **Download** — the verdocumento URL is used verbatim by the central
   ``download_document`` (standard GET path, no inline content).

Every network step is wrapped so a single failure records an error via
``_record_error`` without aborting the rest.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import quote

from ..documents import Document
from ..entities import Entity
from ..oam_base import IssuerRef, OamSource

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_BASE = "https://www.cnmv.es/portal"
_BUSQUEDA_LANDING = _BASE + "/Consultas/BusquedaPorEntidad.aspx?nombre={name}"
_BUSQUEDA_POST = _BASE + "/Consultas/BusquedaPorEntidad"

# Each tuple: (url_path_fragment_with_{nif}_placeholder, doc_type)
_REGISTERS = [
    ("consultas/em_inffinanual.aspx?id=EE&nif={nif}", "annual_report"),
    ("Informacion-Privilegiada/resultado-ip.aspx?nif={nif}", "inside_information"),
    ("Otra-Informacion-Relevante/resultado-oir.aspx?nif={nif}", "other"),
]

# Follow at most this many result pages per register; if there is a next page
# beyond this cap, record a truncation error (same contract as oam_it.py).
_MAX_PAGES = 50

# Heuristic: if a page returns at least this many rows, assume pagination exists.
_PAGE_FULL_THRESHOLD = 10

# ---------------------------------------------------------------------------
# Module-level compiled regexes (same discipline as oam_de.py)
# ---------------------------------------------------------------------------

# Scrape a WebForms hidden input: <input … name="FIELD" … value="VALUE" …>
# The name and value attributes can appear in either order — use separate passes.
_HIDDEN_NAME_RE = re.compile(
    r'<input[^>]+\bname="([^"]+)"[^>]*/?>',
    re.I,
)
_HIDDEN_VALUE_RE = re.compile(
    r'\bvalue="([^"]*)"',
    re.I,
)

# The candidate-issuer select box:
# <option value="NIF">LEGAL NAME</option>
_OPTION_RE = re.compile(
    r'<option(?:\s+[^>]*?)?\s+value="([^"]*)"[^>]*>\s*([^<]*?)\s*</option>',
    re.I | re.S,
)

# The document-row link:
# <a id="…subtituloRegistroEnlace" href="ABSOLUTE_URL" target="_blank"><span …>TITLE</span></a>
_ROW_LINK_RE = re.compile(
    r'<a\s[^>]*\bid="[^"]*subtituloRegistroEnlace"[^>]*\bhref="([^"]+)"[^>]*>'
    r'\s*<span[^>]*>(.*?)</span>',
    re.I | re.S,
)

# Date in Spanish dd/mm/yyyy format — find the FIRST match closest to a row link.
# We look for a date inside the <li class="fecha-con-hora"> element.
_DATE_RE = re.compile(r'(\d{2})/(\d{2})/(\d{4})')

# The verdocumento GUID — extract from the URL for use as doc_id component.
# URL form: …/ver?t=%7b<GUID>%7d   (URL-encoded curly braces)
_GUID_RE = re.compile(r'[Tt]=%7[Bb]([0-9a-fA-F\-]+)%7[Dd]')

# Whitespace collapse for normalisation.
_WS_RE = re.compile(r'\s+')

# Legal-form suffixes to strip when comparing names (trailing, after comma or space).
_LEGAL_SUFFIX_RE = re.compile(
    r',?\s*(?:s\.a\.|s\.a\b|s\.l\.|s\.l\b|s\.a\.u\.|sociedad unipersonal|s\.p\.a\.)$',
    re.I,
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def _normalise(name: str) -> str:
    """Collapse whitespace, casefold, strip a trailing legal-form suffix."""
    n = _WS_RE.sub(' ', name).strip().casefold()
    n = _LEGAL_SUFFIX_RE.sub('', n).strip()
    return n


def _scrape_hidden(html: str, field_name: str) -> str | None:
    """Return the value of a WebForms hidden input by its name attribute."""
    for m in _HIDDEN_NAME_RE.finditer(html):
        if m.group(1) == field_name:
            # Grab the full tag text and look for the value= attribute there.
            tag_text = m.group(0)
            vm = _HIDDEN_VALUE_RE.search(tag_text)
            return vm.group(1) if vm else ''
    return None


def _parse_date(date_text: str) -> str | None:
    """dd/mm/yyyy → ISO date string (YYYY-MM-DD), or None."""
    m = _DATE_RE.search(date_text or '')
    if not m:
        return None
    dd, mm, yyyy = m.groups()
    try:
        return datetime(int(yyyy), int(mm), int(dd)).date().isoformat()
    except ValueError:
        return None


def _extract_guid(url: str) -> str | None:
    """Return the GUID from a verdocumento URL, or None."""
    m = _GUID_RE.search(url)
    return m.group(1).lower() if m else None


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------

class CnmvES(OamSource):
    """Spain OAM backend — resolves issuer name → NIF via CNMV WebForms POST,
    then lists each NIF-keyed register (annual financial reports, inside
    information, other relevant info) parsing static verdocumento rows to stable
    absolute PDF URLs.
    """

    name = "oam-es"
    country = "ES"

    def __init__(self, fetcher=None, config=None):
        super().__init__(fetcher=fetcher, config=config)
        # Per-instance memo: normalised_name → NIF (or None = resolution failed).
        self._nif_cache: dict[str, str | None] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_issuers(self) -> list[IssuerRef]:
        """Return empty — full enumeration is a scale-up concern."""
        return []

    def discover(self, entity: Entity) -> list[Document]:
        if not entity.name:
            return []

        nif = self._resolve_nif(entity.name)
        if nif is None:
            return []

        now = datetime.now(timezone.utc).isoformat()
        out: list[Document] = []

        for url_template, doc_type in _REGISTERS:
            register_url = _BASE + '/' + url_template.format(nif=quote(nif, safe=''))
            try:
                out.extend(
                    self._discover_register(register_url, doc_type, nif, entity, now)
                )
            except Exception as exc:  # noqa: BLE001
                self._record_error('discover', register_url, exc)

        return out

    # ------------------------------------------------------------------
    # Name → NIF resolution
    # ------------------------------------------------------------------

    def _resolve_nif(self, name: str) -> str | None:
        """Resolve an issuer name to its CNMV NIF via WebForms POST.

        Returns the NIF string, or None if the name cannot be resolved
        unambiguously (error is recorded).
        """
        key = _normalise(name)
        if key in self._nif_cache:
            return self._nif_cache[key]

        result = self._do_resolve_nif(name)
        self._nif_cache[key] = result
        return result

    def _do_resolve_nif(self, name: str) -> str | None:
        landing_url = _BUSQUEDA_LANDING.format(name=quote(name))
        try:
            landing_html = self.fetcher.get_text(landing_url)
        except Exception as exc:  # noqa: BLE001
            self._record_error('resolve-landing', landing_url, exc)
            return None

        viewstate = _scrape_hidden(landing_html, '__VIEWSTATE')
        viewstate_gen = _scrape_hidden(landing_html, '__VIEWSTATEGENERATOR')
        event_val = _scrape_hidden(landing_html, '__EVENTVALIDATION')

        if viewstate is None or event_val is None:
            self._record_error(
                'resolve-hidden-fields',
                landing_url,
                RuntimeError('could not scrape __VIEWSTATE / __EVENTVALIDATION from landing'),
            )
            return None

        post_data = {
            '__VIEWSTATE': viewstate or '',
            '__VIEWSTATEGENERATOR': viewstate_gen or '',
            '__EVENTVALIDATION': event_val or '',
            'ctl00$ContentPrincipal$txtBusqueda': name,
            'ctl00$ContentPrincipal$btnBuscar': 'Buscar',
        }
        try:
            results_html = self.fetcher.post_text(_BUSQUEDA_POST, post_data)
        except Exception as exc:  # noqa: BLE001
            self._record_error('resolve-post', _BUSQUEDA_POST, exc)
            return None

        return self._pick_nif(name, results_html, _BUSQUEDA_POST)

    def _pick_nif(self, target_name: str, html: str, url: str) -> str | None:
        """Select the option whose name EXACTLY matches target_name (normalised).

        If zero or more than one option matches, record an error and return None.
        """
        norm_target = _normalise(target_name)
        matches: list[tuple[str, str]] = []  # [(nif, raw_option_name), ...]

        for nif, raw_name in _OPTION_RE.findall(html):
            if _normalise(raw_name) == norm_target:
                matches.append((nif, raw_name))

        if len(matches) == 1:
            return matches[0][0]

        if not matches:
            self._record_error(
                'resolve-no-match',
                url,
                RuntimeError(
                    f"no exact NIF match for '{target_name}' in lstSeleccion "
                    f"(target normalised: '{norm_target}')"
                ),
            )
        else:
            self._record_error(
                'resolve-ambiguous',
                url,
                RuntimeError(
                    f"ambiguous NIF match for '{target_name}': "
                    f"{[nif for nif, _ in matches]}"
                ),
            )
        return None

    # ------------------------------------------------------------------
    # Per-register discovery + pagination
    # ------------------------------------------------------------------

    def _discover_register(
        self, base_url: str, doc_type: str, nif: str, entity: Entity, now: str
    ) -> list[Document]:
        """Paginate one NIF-keyed register and return all Documents found."""
        docs: list[Document] = []
        page = 0
        last_row_count = 0

        while True:
            if page == 0:
                url = base_url
            else:
                sep = '&' if '?' in base_url else '?'
                url = f'{base_url}{sep}page={page}'

            try:
                html = self.fetcher.get_text(url)
            except Exception as exc:  # noqa: BLE001
                self._record_error('register-page', url, exc)
                break

            page_docs = self._parse_register_page(html, doc_type, nif, entity, now)
            docs.extend(page_docs)
            last_row_count = len(page_docs)

            if not page_docs:
                break  # empty page → done

            page += 1

            if page >= _MAX_PAGES:
                # Still rows on the previous page and we've hit the cap.
                if last_row_count >= _PAGE_FULL_THRESHOLD:
                    self._record_error(
                        'truncated',
                        url,
                        RuntimeError(
                            f'register pagination hit the {_MAX_PAGES}-page cap; '
                            'remaining pages not crawled'
                        ),
                    )
                break

        return docs

    def _parse_register_page(
        self, html: str, doc_type: str, nif: str, entity: Entity, now: str
    ) -> list[Document]:
        """Parse one HTML page of a register and return Documents."""
        docs: list[Document] = []

        for m in _ROW_LINK_RE.finditer(html):
            href = m.group(1).strip()
            title_html = m.group(2)
            title = _WS_RE.sub(' ', _TAG_STRIP_RE.sub(' ', title_html)).strip()

            if 'verdocumento/ver' not in href.lower():
                continue

            guid = _extract_guid(href)
            if not guid:
                continue

            # Try to find the date that precedes this row link in the HTML.
            # The fecha-con-hora <li> comes before the link in the page source.
            # We search backwards from the match start for the closest date.
            preceding = html[:m.start()]
            date_m = None
            for date_m in _DATE_RE.finditer(preceding):
                pass  # walk to the last match = closest preceding date
            published_ts = None
            if date_m:
                dd, mm, yyyy = date_m.groups()
                try:
                    published_ts = datetime(int(yyyy), int(mm), int(dd)).date().isoformat()
                except ValueError:
                    pass

            doc_id = f'es-{nif}-{guid}'
            file_name = f'{guid}.pdf'

            doc = Document(
                doc_id=doc_id,
                lei=entity.lei,
                country='ES',
                doc_type=doc_type,
                period_end=None,
                published_ts=published_ts,
                discovered_ts=now,
                language='es',
                source=self.name,
                files=[{'name': file_name, 'kind': 'document', 'url': href}],
                native_meta={'title': title, 'nif': nif, 'guid': guid},
            )
            docs.append(doc)

        return docs


# Strip HTML tags for title extraction (simpler than importing a parser).
_TAG_STRIP_RE = re.compile(r'<[^>]+>')
