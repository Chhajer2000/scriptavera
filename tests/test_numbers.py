# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Numbers on which the readings of a page disagree become doubts for the author, found with no model: the final reading
of a title page wrote 2024 and 2310.16879 where the first look had read 2026 and 2510.16879."""
from helpers import isolate
isolate("scriptavera_numbers_")
from scriptavera.pipeline import number_doubts

final = "Joint work with Xiaolei Wu (Shanghai) arXiv: 2310.16879 Oberseminar Göttingen 15 April 2024 and $x_{2024}$"
ds = number_doubts(final, {"first look at the page": "arXiv 2510.16879, talk on 15 April 2026",
                           "draft reading": "arXiv: 2310.16879 15 April 2024"},
                   ["Changed '15 April 2026' to '15 April 2024' as shown in the manuscript date."])
assert {(d["snippet"], d["alt"]) for d in ds} == {("2310.16879", "2510.16879"), ("2024", "2026")}, ds
print("1. digits that changed between readings are doubts, each once")
assert number_doubts("see page 123 of the 2024 notes", {"draft reading": "see page 123 of the 2024 notes"}, []) == []
assert number_doubts("$a_{123}$ and $b^{124}$", {"draft reading": "$a_{124}$"}, []) == [], "formulas are not compared"
assert number_doubts("Theorem 4.12", {"draft reading": "Theorem 3.1"}, []) == [], "different shapes are different numbers"
print("2. agreeing readings, formulas and unrelated numbers raise nothing")
print("numbers ok")
