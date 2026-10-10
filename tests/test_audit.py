# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from scriptavera.audit import audit, normalize, formulas

assert normalize(r"\left( \frac{x-1}{x+2} \right) \leq 2\,y^{2}.") == normalize(r"(\dfrac{x-1}{x+2})\le 2y^2")
assert normalize(r"\implies w &= \lambda(n)-2k") == normalize(r"w = \lambda(n) - 2k")
pages = [{"n": 3, "text": r"""Then,
$$\begin{aligned} I &= \int_0^1 \left(x^2 + \frac{1}{x+1}\right) dx \\ \implies I &= \frac{1}{3}+\log 2 \end{aligned}$$
Since $\log 2 < 1 \implies I < \frac{4}{3}$ we get $I \ge 0$ here. Also $$\|f\|_2 \le 2(\|f\|_1+1)+1$$ and
$$|B_1 \cup B_2 \cup \cdots \cup B_{j-1}| = 2^j \quad \forall\, 1 \le j \le k$$ and finally $$k \le \lambda(n) - 1$$"""}]
body = r"""We have
\begin{align*}
I &= \int_0^1 \left(x^2 + \frac{1}{x+1}\right) dx,
\end{align*}
which implies \[ I = \frac{1}{3} + \log 2. \]
Since $\log 2 < 1 \Rightarrow I < \frac{4}{3}$, we obtain $I \geq 0$ here. Moreover
\[ \|f\|_2 \le 2(\|f\|_1+1) + 1, \]
and $|B_1 \cup B_2 \cup \cdots \cup B_{k}| \ge 2^j$ for all $1 \le j \le k$. Finally \[ E = mc^2 + 1. \]"""
r = audit(pages, body)
print({k: v for k, v in r.items() if k not in ("altered", "missing", "added")})
print("ALTERED", r["altered"]); print("MISSING", r["missing"]); print("ADDED", r["added"])
assert r["preserved"] >= 4 and len(r["altered"]) == 1 and len(r["missing"]) == 1 and len(r["added"]) == 1, r
print("audit ok")

# ---- changes must never be counted as preserved
SRC = r"$$\int_0^x g(t)\,dt - 1 \le (\lambda(n)-2k)\left(\frac{x-1}{x+2}\right)$$"
def one(body, src=SRC):
    return audit([{"n": 1, "text": src}], body)
for bad in (r"\[\int_0^x g(t)\,dt - 1 \le (\lambda(n)-2k)\left(\frac{x-1}{x-2}\right)\]", r"\[\int_0^x g(t)\,dt - 1 < (\lambda(n)-2k)\left(\frac{x-1}{x+2}\right)\]",
            r"\[\int_0^x g(t)\,dt - 1 \ge (\lambda(n)-2k)\left(\frac{x-1}{x+2}\right)\]", r"\[\int_0^x g(t)\,dt - 1 \le (\lambda(n)-2n)\left(\frac{x-1}{x+2}\right)\]",
            r"\[\int_0^y g(t)\,dt - 1 \le (\lambda(n)-2k)\left(\frac{x-1}{x+2}\right)\]", r"\[\int_0^x g(t)\,dt - 1 \le (\lambda(n)-2k)\left(\frac{x-1}{x+2}\right)^2\]"):
    r = one(bad); assert r["preserved"] == 0 and len(r["altered"]) == 1, (bad, r)
assert one(r"\[ a_n = 1 + kn^2 \]", "$$a_n = 1 + kn$$")["preserved"] == 0
assert one(r"\[ N = 2^a m + 1 \]", "$$N = 2^a m$$")["preserved"] == 0
assert one(r"\[ |B_1 \cup \cdots \cup B_{j+1}| = 2^j \]", r"$$|B_1 \cup \cdots \cup B_{j-1}| = 2^j$$")["preserved"] == 0
# ---- legitimate rewrites must not raise alarms
ok = [
 (SRC, r"\begin{equation}\label{eq:a} \int_{0}^{x} g(t)\,dt - 1 \leq (\lambda(n)-2k)(\dfrac{x-1}{x+2}), \end{equation}"),
 (r"$x \in A \Rightarrow x^2 \in B \Rightarrow x^4 \le 1$", r"If $x \in A$, then $x^2 \in B$, and therefore $x^4 \le 1$."),
 (r"$$\begin{aligned} a &= b \\ \implies d &\mid m \end{aligned}$$", r"We have $a = b$, so $d \mid m$."),
 (r"$$a_n = 1 \text{ or } a_n = q$$", r"Either $a_n = 1$ or $a_n = q$."),
 (r"$$a + b = c + d = e + f$$", r"\begin{align*} a + b &= c + d \\ &= e + f. \end{align*}"),
 (r"$$|B_1 \cup \cdots \cup B_{j-1}| = 2^j \quad \forall\, 1 \le j \le k$$", r"\[ |B_1 \cup \cdots \cup B_{j-1}| = 2^j \] for all $1 \le j \le k$."),
 (r"$w = \lambda(n) - 2k$", r"Lemma. \[ w = \lambda(n) - 2k. \] Proof: recall $w = \lambda(n)-2k \ge 0$."),
 (r"$$\begin{aligned} w &= x - y \\ \implies w &= \lambda(n)-2k \end{aligned}$$", r"\begin{align*} w &= x - y, \end{align*} which gives \[ w = \lambda(n) - 2k. \]"),
]
for src, body in ok:
    r = one(body, src); assert r["preserved"] == r["total"] and not r["altered"] and not r["missing"], (src, body, r)
print("strict audit ok")
