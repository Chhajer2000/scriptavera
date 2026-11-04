# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Step 9, a crowded page: where the matching by length is not sure, a model that sees the page (framed and numbered)
says which piece stands on which line, in the background; its answer is kept for the text it was given and used when
the page is shown; a new text asks again; a paper can turn it off; a failure leaves the page as it was."""
import os
import time
from helpers import isolate, new_project
isolate("scriptavera_placing_")
from scriptavera import placing, pipeline

p = new_project(pages=1)
# a transcript with more pieces than the page has lines: the matching by length cannot be sure of it
text = "\n".join(f"Line {k}: the series converges for every positive exponent" for k in range(1, 9))
p.save("transcript", {"pages": {"1": {"text": text}}})


def settle(n=1, seconds=20):
    t0 = time.time()
    while placing.busy(p, n, p.load("transcript")["pages"][str(n)]["text"]) and time.time() - t0 < seconds:
        time.sleep(0.1)


first = pipeline.page_layouts(p)["1"]
assert first.get("doubt") and first.get("placing"), {k: first.get(k) for k in ("ok", "doubt", "placing", "cost")}
settle()
got = placing.cached(p, 1, text)
assert got and got.get("assign"), got
again = pipeline.page_layouts(p)["1"]
assert again.get("ai") and not again.get("placing"), again.get("ai")
print("1. a doubtful page is placed by the model in the background, and shown as placed once its answer is in")

p.save("transcript", {"pages": {"1": {"text": text + "\nOne more line written at the end"}}})
assert placing.cached(p, 1, text + "\nOne more line written at the end") is None
assert pipeline.page_layouts(p)["1"].get("placing"), "a new text is placed again"
settle()
print("2. a changed page is asked about again")

p.update_state(lambda st: st.update(layout_ai=False))
p.save("transcript", {"pages": {"1": {"text": text + "\nAnd another"}}})
off = pipeline.page_layouts(p)["1"]
assert not off.get("placing") and not off.get("ai")
p.update_state(lambda st: st.update(layout_ai=True))
print("3. turned off for the paper: nothing is asked")

os.environ["SCRIPTAVERA_MOCK_FAIL"] = "Place the lines"
pipeline.page_layouts(p)
settle()
failed = placing.cached(p, 1, text + "\nAnd another")
assert failed and failed.get("failed"), failed
assert not pipeline.page_layouts(p)["1"].get("placing"), "a failure is not asked again at once"
os.environ.pop("SCRIPTAVERA_MOCK_FAIL")
print("4. a failure leaves the page laid out by length, and is not retried at once")
print("placing ok")
