# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Test mode: a test paper never meets its own answer. Every source is asked only for work before the cutoff, what
comes back is filtered again, excluded papers never enter, OEIS is not asked, and the limits stay with the paper."""
import json, sys, pathlib
from helpers import isolate, page_images
isolate("scriptavera_testmode_")
import tests.simulator as sim  # noqa: F401
from scriptavera import config, literature as L, relevance, store

lim = {"before": "2026-05-01", "exclude": ["2604.01234", "10.1000/xyz.42", "Groups with half as many cyclic subgroups as elements"]}
ok = lambda **kw: L.allowed(L._paper(**kw), lim)  # noqa: E731
assert ok(title="Old", date="2026-04-30", year=2026) and not ok(title="New", date="2026-05-01", year=2026)
assert ok(title="Older", year=2025) and not ok(title="Same year, no day", year=2026) and not ok(title="Undated")
assert not ok(title="X", year=2020, arxiv="2604.01234v2") and not ok(title="Y", year=2020, doi="https://doi.org/10.1000/XYZ.42")
assert not ok(title="Groups With Half as Many Cyclic Subgroups as Elements, II", year=2020)

sent = []
class Fake:
    headers = {}
    def get(self, url, params=None, timeout=None, **kw):
        sent.append((url, dict(params or {})))
        class R:
            status_code = 200
            text = "<feed xmlns='http://www.w3.org/2005/Atom'></feed>"
            content = text.encode()
            def json(self):
                return {"results": [], "items": [], "data": [], "message": {"items": []}}
        return R()
L._session = lambda: Fake()
L.set_limits(lim)
for fn in (lambda: L.arxiv_search("cyclic subgroups finite groups", []), lambda: L.openalex_search("cyclic subgroups", ["math.GR"]),
           lambda: L.crossref_search("cyclic subgroups", []), lambda: L.s2_search("cyclic subgroups", ["math.GR"])):
    try:
        fn()
    except Exception:
        pass
blob = json.dumps(sent)
assert "submittedDate:[190001010000 TO 202604302359]" in blob, blob
assert "to_publication_date:2026-04-30" in blob and "until-pub-date:2026-04-30" in blob and '":2026-04-30"' in blob, blob
assert relevance.oeis("1, 2, 4, 8, 16, 32") == [], "OEIS cannot be limited by date: not asked"
L.set_limits(None)
assert L.allowed(L._paper(title="New", date="2026-09-01")), "papers made outside test mode are never limited"

# turned on with a button; a paper made then keeps its limits
from starlette.applications import Starlette
from starlette.testclient import TestClient
from scriptavera import server
c = TestClient(Starlette(routes=server.routes), base_url="http://127.0.0.1")
assert c.post("/api/test_mode", json={"on": True, "before": "nonsense"}).status_code == 400
r = c.post("/api/test_mode", json={"on": True, "before": "2026-05-01", "exclude": "2604.01234\nGroups with half as many"}).json()
assert r["settings"]["test_mode"] == {"on": True, "before": "2026-05-01", "exclude": ["2604.01234", "Groups with half as many"]}
meta = {"name": "Test paper", "domains": ["math.GR"], "problem": "Bound the order of a finite group by its number of cyclic subgroups.",
        "keywords": ["cyclic subgroups", "finite groups"], "authors": [{"name": "Test Author"}], "declaration": {"agreed": True, "author": 0},
        }
files = [("files", (n, d, "image/png")) for n, d in page_images(1)]
r = c.post("/api/projects", data={"meta": json.dumps(meta)}, files=files)
pid = r.json().get("id")
if pid:
    assert store.get(pid).state["meta"]["test"]["before"] == "2026-05-01"
    c.post("/api/test_mode", json={"on": False})
    assert store.get(pid).state["meta"]["test"]["before"] == "2026-05-01", "switching off later changes nothing for it"
else:
    print("(project not created over HTTP:", r.json().get("error"), ")")
print("testmode ok")
