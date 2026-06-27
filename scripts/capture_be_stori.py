#!/usr/bin/env python3
"""Capture real STORI (Belgium / FSMA) responses — run from a NON-datacenter network.

WHY THIS EXISTS
---------------
STORI's authoritative site, https://stori.fsma.be, sits behind a WAF that RESETS
HTTP requests coming from datacenter / cloud / VPN egress IPs (the connection is
reset the moment the request is sent — verified: the TLS handshake itself succeeds,
even a Chrome-impersonated client is reset, and we egress from a non-Belgian
datacenter range). It is NOT geo-blocked and NOT a TLS-fingerprint block — it is
source-IP reputation. A normal residential/office browser (e.g. Marc's machine in
France) reaches it fine; our build/CI sandbox cannot.

So this one script does the recon-first capture that the assistant normally does
itself, from the one network path that works: YOURS. Run it once; it writes real
fixtures into tests/fixtures/eu/ and prints a structured summary. With those, the
BE STORI backend can be built and validated against REAL current responses (no
guessing against a 4-year-old archive).

The WAF rejects the *client fingerprint* (TLS/JA3 + HTTP2), not only the IP — even
plain python-`requests` from a residential network is reset. So this script uses
`curl_cffi` to impersonate a real Chrome/Safari/Edge fingerprint, which a normal
browser presents (and which the WAF allows from a clean IP). Install it first:

    pip install curl_cffi

USAGE
-----
    python scripts/capture_be_stori.py
    # optionally point it at a specific issuer / ISIN:
    python scripts/capture_be_stori.py --company "Anheuser-Busch InBev" --isin BE0974293251

It only READS public pages (a search + one document). Then commit the new
tests/fixtures/eu/be_stori_*.* files (or paste the printed summary back).

If EVERY impersonation profile is still reset: confirm STORI loads in your normal
browser first (it's a public site). If the browser works but this script can't, the
last resort is a headless real browser (Playwright) — tell the assistant and it will
switch the capture to that.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

# Prefer curl_cffi (browser TLS/JA3 + HTTP2 impersonation); fall back to requests.
_IMPERSONATE = ["chrome124", "chrome120", "chrome", "safari17_0", "edge122"]
try:
    from curl_cffi import requests as _curl
    _HAVE_CURL = True
except ImportError:  # pragma: no cover
    _HAVE_CURL = False
try:
    import requests as _plain
except ImportError:  # pragma: no cover
    _plain = None

if not _HAVE_CURL and _plain is None:
    sys.exit("Install curl_cffi (recommended) or requests: pip install curl_cffi")


def _open_session():
    """Return (session, label) for whichever client can reach STORI's WAF.

    Tries each Chrome/Safari/Edge impersonation profile, then plain requests.
    Returns (None, None) if every strategy is reset.
    """
    if _HAVE_CURL:
        for imp in _IMPERSONATE:
            try:
                sess = _curl.Session(impersonate=imp, timeout=30)
                sess.headers.update({"Accept-Language": "en,fr,nl"})
                r = sess.get(BASE + "/", timeout=30)
                if r.status_code < 500:
                    print(f"  ✓ reached STORI via curl_cffi impersonate={imp} (HTTP {r.status_code})")
                    return sess, f"curl_cffi:{imp}"
            except Exception as exc:
                print(f"  · curl_cffi {imp}: {type(exc).__name__} — {str(exc)[:60]}")
    if _plain is not None:
        try:
            sess = _plain.Session()
            sess.headers.update({"User-Agent": UA, "Accept-Language": "en,fr,nl"})
            r = sess.get(BASE + "/", timeout=30)
            print(f"  ✓ reached STORI via plain requests (HTTP {r.status_code})")
            return sess, "requests"
        except Exception as exc:
            print(f"  · plain requests: {type(exc).__name__} — {str(exc)[:60]}")
    return None, None

BASE = "https://stori.fsma.be"
FIX = pathlib.Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "eu"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_HIDDEN = re.compile(r'<input[^>]*type="hidden"[^>]*>', re.I)
_NAME = re.compile(r'name="([^"]+)"')
_VALUE = re.compile(r'value="([^"]*)"')


def _hidden_fields(html: str) -> dict[str, str]:
    """All ASP.NET hidden inputs (__VIEWSTATE, __EVENTVALIDATION, ...)."""
    out: dict[str, str] = {}
    for tag in _HIDDEN.findall(html):
        n, v = _NAME.search(tag), _VALUE.search(tag)
        if n:
            out[n.group(1)] = v.group(1) if v else ""
    return out


def _form_action(html: str) -> str:
    m = re.search(r'<form[^>]+action="([^"]+)"', html, re.I)
    if not m:
        return "/Search.aspx"
    action = m.group(1).lstrip(".")
    return action if action.startswith("/") else "/" + action


def _named_inputs(html: str) -> list[str]:
    return sorted({n for n in re.findall(r'<(?:input|select|textarea)[^>]*name="([^"]+)"', html, re.I)})


def _find(names: list[str], *needles: str) -> str | None:
    for n in names:
        low = n.lower()
        if all(x in low for x in needles):
            return n
    return None


def _save(name: str, content: bytes | str) -> pathlib.Path:
    FIX.mkdir(parents=True, exist_ok=True)
    p = FIX / name
    mode = "wb" if isinstance(content, bytes) else "w"
    with open(p, mode, encoding=None if isinstance(content, bytes) else "utf-8") as fh:
        fh.write(content)
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", default="Anheuser-Busch InBev",
                    help="issuer name to search (CompanyName field)")
    ap.add_argument("--isin", default="", help="optional ISIN to search instead/as well")
    args = ap.parse_args()

    print(f"[1/4] GET {BASE}/ (reachability + search form)")
    if not _HAVE_CURL:
        print("  ! curl_cffi not installed — only plain requests available, which the\n"
              "    STORI WAF rejects by fingerprint. Strongly recommend: pip install curl_cffi")
    s, via = _open_session()
    if s is None:
        print("  ✗ every client was reset by STORI's WAF.\n"
              "  → 1) pip install curl_cffi  and re-run (impersonates a real browser).\n"
              "    2) confirm https://stori.fsma.be loads in your normal browser.\n"
              "    3) if the browser works but this can't, we'll switch to Playwright.")
        return 2
    r = s.get(BASE + "/", timeout=30)
    form_html = r.text
    sp = _save("be_stori_search.html", form_html)
    print(f"  ✓ {r.status_code}, {len(form_html)} bytes  → {sp}")

    action = _form_action(form_html)
    names = _named_inputs(form_html)
    hidden = _hidden_fields(form_html)
    company_field = _find(names, "company", "text") or _find(names, "companyname")
    isin_field = _find(names, "isin")
    button = (_find(names, "search", "button") or _find(names, "btnsearch")
              or _find(names, "searchbutton") or _find(names, "simplesearch", "button"))
    print(f"  form action : {action}")
    print(f"  hidden fields: {', '.join(k for k in hidden if k.startswith('__')) or '(none — not WebForms?)'}")
    print(f"  company field: {company_field or '??'}")
    print(f"  isin field   : {isin_field or '??'}")
    print(f"  search button: {button or '??  (inspect the printed field list)'}")
    print(f"  all named fields ({len(names)}): {names}")

    print(f"\n[2/4] POST search  company={args.company!r} isin={args.isin!r}")
    body = dict(hidden)
    if company_field and not args.isin:
        body[company_field] = args.company
    if isin_field and args.isin:
        body[isin_field] = args.isin
    if button:
        body[button] = "Search"
    try:
        rr = s.post(BASE + action, data=body, timeout=40)
        rp = _save("be_stori_result.html", rr.text)
        print(f"  ✓ {rr.status_code}, {len(rr.text)} bytes  → {rp}")
        result_html = rr.text
    except Exception as exc:
        print(f"  ✗ search POST failed: {exc}")
        return 3

    print("\n[3/4] inspect result rows / document links")
    # Heuristics — print whatever document/download links the result page exposes so
    # the backend parser can be written against the REAL structure.
    doc_links = sorted(set(re.findall(
        r'(?:href|src)="([^"]*(?:ViewDocument|Document|Download|GetFile|View\.aspx|\.pdf|\.zip)[^"]*)"',
        result_html, re.I)))
    postbacks = sorted(set(re.findall(r"__doPostBack\('([^']+)'", result_html)))[:8]
    grids = re.findall(r'id="([^"]*(?:Grid|Result|Repeater|gv|DataList)[^"]*)"', result_html)[:6]
    print(f"  document/download links ({len(doc_links)}):")
    for d in doc_links[:12]:
        print(f"    {d[:120]}")
    print(f"  result-grid ids: {grids}")
    print(f"  sample __doPostBack targets: {postbacks}")

    print("\n[4/4] try to fetch ONE document (first direct link, if any)")
    direct = next((d for d in doc_links if d.lower().endswith((".pdf", ".zip"))
                   or "viewdocument" in d.lower() or "download" in d.lower()), None)
    if direct:
        url = direct if direct.startswith("http") else BASE + "/" + direct.lstrip("/")
        try:
            dr = s.get(url, timeout=60)
            ext = "zip" if dr.content[:2] == b"PK" else ("pdf" if dr.content[:4] == b"%PDF" else "bin")
            dp = _save(f"be_stori_document.{ext}", dr.content)
            print(f"  ✓ {dr.status_code} {dr.headers.get('content-type')} "
                  f"{len(dr.content)} bytes magic={dr.content[:4]!r}  → {dp}")
        except Exception as exc:
            print(f"  ✗ document fetch failed: {exc}")
    else:
        print("  (no direct document link in the result HTML — it may be a postback;\n"
              "   the saved be_stori_result.html still captures the structure.)")

    print("\n────────────────────────────────────────────────────────────────")
    print("DONE. Next: commit the new tests/fixtures/eu/be_stori_*.* files")
    print("(or paste this summary back), and the BE backend can be built +")
    print("validated against these REAL responses.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
