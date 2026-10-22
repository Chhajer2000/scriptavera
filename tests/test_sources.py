# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""From the paper back to the handwriting: the passage around a line, and the page of the notes it came from."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from scriptavera import sources as S

tex = r"""\section{Bounds}
For every $n \ge 1$ we have the following.

\begin{align*}
H_n &\le 1 + \log n \\
&\le 2n.
\end{align*}
The proof is by comparison with an integral.
"""
assert S.passage_at(tex, 5).startswith("\\begin{align*}") and S.passage_at(tex, 5).rstrip().endswith("\\end{align*}")
assert "For every" in S.passage_at(tex, 2)
pages = [{"n": 1, "text": r"Intro words about harmonic numbers and their history."},
         {"n": 2, "text": r"Compare with $\int_1^n dx/x$: $$H_n \le 1 + \log n$$ and so $H_n \le 2n$ for every $n$."},
         {"n": 3, "text": r"Groups of order $p^2$ are abelian."}]
hit = S.find_page(pages, S.passage_at(tex, 5))
assert hit and hit["page"] == 2 and hit["formulas"], hit
assert S.find_page(pages, "Acknowledgements: we thank the referee for careful reading.") is None
print("sources ok")
