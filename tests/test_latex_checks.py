# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""LaTeX checks: packages loaded for the commands a paper uses, undefined commands found by asking TeX itself (never
guessed from an error line), formulas too wide for the page broken over lines by rule, and the fidelity audit still
recognising a formula after it has been broken."""
import shutil, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from scriptavera import paper as P
from scriptavera.audit import audit, normalize

# packages follow the commands used
assert "\\usepackage{cancel}" in P.needed_packages(r"$\cancel{6}_3$"), P.needed_packages(r"$\cancel{6}_3$")
assert P.needed_packages(r"$x + y \le z$") == []
assert any("mathrsfs" in l for l in P.needed_packages(r"$\mathscr{F}$"))

# breaking: a long relation chain becomes split, aligned at a relation; a long sum with the relation at the end multlined
user = (r"2(3^\alpha - 1) - 4k + \cancel{4}^2 \left[ \frac{\overbrace{\cancel{8}}^{\phi(3^3)\ \text{times}}"
        r"}{\underbrace{\cancel{6}_3}_{\phi(9)}} \right] + 3^{\alpha-1}(3-1) + 2(3^{\alpha-2}+3^{\alpha-3}+\cdots+1) \le s")
b = P.break_display(user)
assert b and "\\\\" in b and ("multlined" in b or "split" in b), b
assert normalize(b.replace("\\\\", " ")) .replace(" ", "") != "", b
chain = r"f(x) = \sum_{k=1}^{n} \binom{n}{k} x^k (1-x)^{n-k} + \int_0^1 t^{n} (1-t)^{m} \, dt = (1 + x)^n - 1 + B(n+1, m+1) = g(x) + h(x) + \cdots"
b2 = P.break_display(chain)
assert b2 and b2.strip().startswith("\\begin{split}") and "&=" in b2, b2
assert P.break_display(r"x \in A") is None          # nothing worth breaking

# the audit still finds a broken formula on the notes page, and still catches a real change
page = [{"n": 1, "text": "$$" + chain + "$$"}]
body = "\\[\n" + b2 + "\n\\]"
r = audit(page, body)
assert r["preserved"] == r["total"] == 1, r
r2 = audit(page, body.replace("(1 + x)^n - 1", "(1 + x)^n + 1"))
assert r2["preserved"] < r2["total"], r2

if shutil.which("pdflatex"):
    doc = (r"\documentclass{amsart}\usepackage{amsmath,amssymb}" "\n" + "\n".join(P.needed_packages(r"\cancel"))
           + r"\newcommand{\Aut}{\operatorname{Aut}}\begin{document}$\cancel{6} \Aut \phi \Zeta$\end{document}")
    und = P.undefined_commands(doc)
    assert und == ["Zeta"], und           # \cancel (package), \phi (LaTeX), \Aut (defined) are not undefined
print("latex checks ok")
