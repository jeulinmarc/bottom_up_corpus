from bottom_up_corpus.eu.entities import Entity
from bottom_up_corpus.eu.financials import facts_for_entity


class FakeFetcher:
    """Routes get_json by URL: the entity filings list vs each report json_url."""
    def __init__(self, filings, reports):
        self._filings = filings        # list of filing attribute dicts
        self._reports = reports        # {json_url_path: report dict}
    def get_json(self, url, **kw):
        if "/api/entities/" in url:
            return {"data": [{"id": a["fxo_id"], "attributes": a} for a in self._filings]}
        path = url.replace("https://filings.xbrl.org", "")
        return self._reports[path]


def _report(concept_val):
    return {"facts": {"f": {"value": concept_val, "dimensions": {
        "concept": "ifrs-full:Revenue", "entity": "x", "unit": "iso4217:EUR",
        "period": "2020-01-01T00:00:00/2021-01-01T00:00:00"}}}}


def test_facts_for_entity_unions_filings():
    filings = [{"fxo_id": "1", "country": "FR", "period_end": "2020-12-31",
                "date_added": "2021-05-01 00:00:00",
                "json_url": "/r/2020.json", "package_url": "/r/2020.zip", "report_url": "/r/2020.html"}]
    fetcher = FakeFetcher(filings, {"/r/2020.json": _report(100)})
    ent = Entity(lei="LEI123", name="X", country="FR")
    flat = facts_for_entity(ent, fetcher=fetcher)
    assert "Revenue" in flat
    assert flat["Revenue"][0]["val"] == 100
    assert flat["Revenue"][0]["filed"] == "2021-05-01 00:00:00"
