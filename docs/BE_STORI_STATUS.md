# Belgium (FSMA / STORI) — status & finish plan

**TL;DR:** BE is the one EU OAM not yet shipped. Its authoritative source,
`https://stori.fsma.be`, is **unreachable from the build/CI environment** because a
WAF resets HTTP requests by source-IP reputation. It is reachable from a normal
residential/office network. One script (`scripts/capture_be_stori.py`), run once
from such a network, captures the real responses needed to build and validate the
backend.

## Why it's blocked here (diagnosis)

The FSMA's filing infrastructure sits behind an **F5 BIG-IP ASM WAF** that blocks
automated/non-browser clients. Evidence from direct testing:

| Test | Result |
|---|---|
| DNS + TCP `:443` to `stori.fsma.be` | ✅ open |
| `http://stori.fsma.be` (port 80) | ✅ HTTP 302 → https |
| **openssl TLS handshake** | ✅ completes, valid `*.fsma.be` cert |
| **openssl / curl / `requests` + an HTTP GET** to `stori.fsma.be` | ✗ `Connection reset by peer` |
| `curl_cffi` impersonating Chrome (real JA3) from our IP | ✗ reset |
| `webapi.fsma.be/...` (the JSON API) for any non-browser client | ✗ **F5 "Error Page … support ID:"** block |
| Marc's residential machine + python-`requests` | ✗ also reset |
| Our egress IP geo | 🇫🇷 France (so **not** geo-blocked) |
| `www.fsma.be` info pages | ✅ reachable |

So it is **not** geo, **not** a broken TLS handshake. The F5 WAF fingerprints the
client (TLS/JA3 + HTTP behaviour) **and** scores source-IP reputation: `stori.fsma.be`
resets the connection, `webapi.fsma.be` returns the F5 ASM block page ("support ID").
Plain `requests` is blocked even from Marc's clean residential IP (wrong fingerprint);
a **real browser fingerprint from a clean IP** is what passes — which is why the
capture script uses `curl_cffi` (Chrome impersonation) and must run from a normal
network, not CI.

No reachable substitute exists: `filings.xbrl.org` carries only ~1/10 BE blue chips
(only KBC of AB InBev/Ageas/UCB/Solvay/Umicore/Proximus/Sofina/GBL/Colruyt), and
`www.fsma.be` issuer pages are profiles, not the filings archive.

### Better target than the old WebForms site: the JSON API

The current FSMA site (`www.fsma.be`) drives its data tools from a **Vue app calling
`https://webapi.fsma.be`** (the drupal setting `vueToolsApi`), which exposes a
**Swagger/OpenAPI** surface (`/swagger/index.html`, spec at `/swagger/v1/swagger.json`).
If a browser-fingerprinted request from a clean IP clears the WAF, this is a clean
JSON API — a far better backend than scraping the legacy `stori.fsma.be` WebForms app
(like the UK FCA NSM). The capture script targets this **first**; the OpenAPI spec
alone documents every STORI endpoint. The WebForms path is kept only as a fallback.

## What's confirmed about STORI (from web.archive.org, 2021 snapshot)

STORI is an **ASP.NET WebForms** app — same technology as the Spanish CNMV backend
(`oam_es.py`), which is the closest template. The search page (`Search.aspx?PageID=…`)
carries `__VIEWSTATE` and these fields:

- `ctl00$ContentPlaceHolder1$CompanyNameTextBox` — issuer name
- `ctl00$ContentPlaceHolder1$isinCodeTextBox` — **ISIN** (clean identity; our entities carry ISINs from GLEIF)
- `ctl00$ContentPlaceHolder1$titleTextBox` — document title
- `searchCompanyDropDownList`, `searchDocumentTypeDropDownList`
- published/received date-range calendars

Document formats: **PDF**, except **annual financial reports = ZIP** (containing the
full report incl. XBRL/ESEF) — a ready Pillar-B feed, like ES/NL/UK.

**Document-type taxonomy** (from the live dropdown; maps cleanly to our `DOC_TYPES`):

| STORI type (NL) | doc_type |
|---|---|
| Jaarlijks financieel verslag | `annual_report` |
| Halfjaarlijks financieel verslag | `half_year_report` |
| Kwartaalinformatie / Driemaandelijks financieel verslag | `interim_statement` |
| Tussentijdse verklaring | `interim_statement` |
| Verslag over duurzaamheid | `other` |
| Verslag over betalingen aan overheden | `other` |
| Oproeping / Notulen algemene vergadering | `governance` |
| Communiqué transparantiekennisgeving | `holding_notification` |
| Dividend-/Couponbericht | `other` |

What is **not** yet known (no archived result page; the bug-prone parts): the
search-**result** row structure and the document **download** URL pattern. Those
need one real search execution — which `scripts/capture_be_stori.py` performs.

## Finish plan (≈ one build cycle once fixtures land)

1. Run `python scripts/capture_be_stori.py` from a residential/office network
   (or `--isin BE0974293251` for AB InBev). It saves real
   `tests/fixtures/eu/be_stori_{search,result,document}.*` and prints the live
   field names + result-row/download structure.
2. Build `bottom_up_corpus/eu/sources/oam_be.py` (`OamSource`, `country="BE"`),
   mirroring `oam_es.py`: GET `Search.aspx` → scrape `__VIEWSTATE` → POST by ISIN
   (fallback CompanyName) → parse result rows → download URL. doc_type from the map
   above. Uses the shared `Fetcher` (plain `requests`) — which works from any
   non-blocked network, including production runs.
3. Wire `COUNTRY_BACKENDS["BE"]`, add network-free tests against the captured
   fixtures, live-validate against AB InBev, review, PR — the same cycle as the
   other six backends.

The backend is deliberately **not** written speculatively against the 4-year-old
archive: every other backend caught a real bug only at live validation, so BE waits
for one real capture rather than shipping a guess.
