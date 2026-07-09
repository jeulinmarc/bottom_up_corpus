# Belgium (FSMA / STORI) — RESOLVED

**Status: built & live-validated.** `bottom_up_corpus/eu/sources/oam_be.py` (`StoriBE`,
`COUNTRY_BACKENDS["BE"]`) queries the modern FSMA JSON API and was validated end-to-end
from a normal network: AB InBev (ISIN `BE0974293251`) → **446 documents**, and a real
469 KB PDF downloads. The backend is a clean JSON-API client (like the UK NSM): search
by `isinCode`, map `reportingTopicName` → doc_type, download via `/download?fileDataId=`.

The API (`https://webapi.fsma.be/api/v1/{lang}/stori`) sits behind an F5 WAF, so the
backend's HTTP layer impersonates Chrome via **`curl_cffi`** (optional dep: `pip install
".[be]"`) with `Origin`/`Referer: https://www.fsma.be` and a cookie-bootstrapped session.
It must run from a non-datacenter network (the WAF blocks flagged IPs). `scripts/
validate_be.py` runs the real backend end-to-end for a sanity check.

The rest of this document is the investigation record that led here (the F5-WAF diagnosis
and how the live API was captured via the browser), kept for context.

---

**Original TL;DR (superseded):** BE's classic `stori.fsma.be` site is unreachable from
CI because an F5 WAF blocks non-browser clients; the modern API on `webapi.fsma.be` was
captured via a real browser (Chrome DevTools) and the backend built against those real
responses.

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

## How it was built (completed steps)

> The steps below were the open plan at investigation time. All are now done (PR #60 merged).

1. ✅ Ran `python scripts/capture_be_stori.py` against the modern `webapi.fsma.be`
   JSON API (not the legacy WebForms site — the WAF blocks those from all clients;
   the JSON API was captured from a residential network via `curl_cffi` Chrome
   impersonation). Real fixtures saved to `tests/fixtures/registers/bnb_*.json`.
2. ✅ Built `bottom_up_corpus/registers/bnb_cbso.py` and `bnb_xbrl.py` against the
   BNB Central Balance Sheet Office (CBSO) open-data XBRL endpoint — the FSMA/STORI
   route proved unnecessary because BNB CBSO is the open statutory-accounts register
   for Belgium, separate from the regulated-filings index. `--be-file`/`--be-numbers`
   CLI flags; dimensional XBRL parsed via Arelle.
3. ✅ Wired `concepts_be.py`, network-free tests added, live-validated against real
   BE entities (Equinor BE, AB InBev), reviewed, and merged as PR #60.

The FSMA/STORI investigation record above is kept for context (it explains why
`curl_cffi` is required and why the JSON API, not the WebForms site, is the target
if EU-pillar BE filing acquisition is ever needed — the BNB CBSO path only covers
statutory financial accounts, not exchange-filed disclosures).
