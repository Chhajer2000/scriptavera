# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Relevance: vector similarity of each result to each paper, bibliographic coupling and co-citation with the key
papers, the combined ranking, and the edges of the map of papers."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from helpers import isolate
isolate("scriptavera_relevance_")
from scriptavera import relevance as R

lib = [
    {"key": "A", "title": "Cyclic subgroups of finite groups", "abstract": "We count cyclic subgroups of a finite group and bound the order.", "openalex": "W1", "lex": 6},
    {"key": "B", "title": "Heat kernels on manifolds", "abstract": "Gaussian bounds for the heat kernel on Riemannian manifolds.", "openalex": "W2", "lex": 1},
    {"key": "C", "title": "Groups with few cyclic subgroups", "abstract": "Finite groups whose number of cyclic subgroups is small relative to the order.", "openalex": "W3", "lex": 4},
    {"key": "K", "title": "Key paper on cyclic subgroup counts", "abstract": "Counting cyclic subgroups.", "openalex": "W9", "lex": 5, "pinned": True},
]
refs = {"W1": {"refs": {"W50", "W51", "W52", "W9"}, "related": set()}, "W2": {"refs": {"W70", "W71"}, "related": set()},
        "W3": {"refs": {"W50", "W51", "W60"}, "related": {"W1"}}, "W9": {"refs": {"W50", "W51", "W52", "W1"}, "related": {"W3"}}}
g = R.graph(lib, [lib[3]], refs=refs)
assert g["papers"]["W1"]["coupling"] > g["papers"]["W3"]["coupling"] > g["papers"]["W2"]["coupling"] == 0, g["papers"]
assert g["papers"]["W1"]["cocited"] == 1 and g["papers"]["W3"]["related"] == 1
assert any({e[0], e[1]} == {"W1", "W9"} for e in g["edges"]) and not any("W2" in e[:2] for e in g["edges"])
res = [{"id": "R1", "statement": "If a finite group G has exactly |G|/2 cyclic subgroups then the order of G is bounded."}]
close, how = R.closest(res, lib, g)
assert "TF-IDF" in how and close["R1"][0]["key"] in ("A", "C", "K") and close["R1"][-1]["key"] != "A", close
assert [x["key"] for x in close["R1"]].index("B") if "B" in [x["key"] for x in close["R1"]] else True
assert R.oeis("1, 2, 4, 8, 16") == [] and R.threads(["cyclic subgroups"]) == []      # nothing leaves the computer in tests
# Private mode: only models Google sells on its paid tier, or local ones
from scriptavera.llm import private_ok
on = {"private": True, "private_models": ["gemini:gemini-4-argon"]}
assert private_ok("gemini", "gemini-3.8-flash", on) and private_ok("gemini", "gemini-3.1-pro-preview", on)
assert not private_ok("gemini", "gemini-3.7-flash", on) and not private_ok("gemini", "gemini-3.5-flash", on)
assert not private_ok("groq", "openai/gpt-oss-20b", on) and private_ok("ollama", "qwen3:8b", on)
assert private_ok("gemini", "gemini-4-argon", on) and private_ok("groq", "openai/gpt-oss-20b", {"private": False})
print("relevance ok")
