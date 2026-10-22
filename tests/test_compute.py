# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Computer checks of proofs: steps cut by code, read exactly, typed from the confirmed notation; identities proved,
integer claims checked case by case, steps that follow from earlier lines recognised, the smallest counterexample
found, and nothing reported as false that the proof justifies by a reason or that only introduces a symbol."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from scriptavera import compute as C

if not C.AVAILABLE:
    print("compute skipped (SymPy not installed)")
    sys.exit(0)

GL = [{"symbol": "s", "meaning": "the number of elements of order 3"}, {"symbol": "G", "meaning": "a finite group"},
      {"symbol": "i(G)", "meaning": "the number of cyclic subgroups of G"}]
ctx = C._Ctx(GL)
# reading: hand cancellations keep their value, braces with labels are annotations, |G| is an opaque positive number
e = C.read(r"2(3^\alpha - 1) - 4k + \cancel{4}^2 \left[ \frac{\overbrace{\cancel{8}}^{\phi(3^3)\ \text{times}}}"
           r"{\underbrace{\cancel{6}_3}_{\phi(9)}} \right]", ctx)
a, k = ctx.sym("\\alpha"), ctx.sym("k")
assert C.sp.simplify(e - (2 * 3 ** a - 4 * k + C.sp.Rational(10, 3))) == 0, e
assert C.read(r"\frac{|G|}{2}", ctx).free_symbols == {ctx.sym("|G|")}
assert str(C.read(r"\binom{n}{2} + \lfloor n/2 \rfloor + n!", ctx)).count("binomial") == 1
try:
    C.read(r"1 + 2 + \cdots + n", ctx)
    raise AssertionError("dots must not be read")
except C.Unreadable:
    pass

stmt = r"Let $n \ge 1$ and $k \ge 1$ be integers."
proof = r"""We have
\[ (a+b)^2 = a^2 + 2ab + b^2 \le 2a^2 + 2b^2. \]
Also $\binom{n}{2} = \frac{n(n-1)}{2}$, so $\binom{n}{2} + n = \frac{n(n+1)}{3}$.
Since $|G| \le 2^k$, we get $|G|^2 \le 4^k$. By Lemma 2, $|G| = 3s + 1$, so $|G|^2 \le 2^k$.
Hence
\begin{align*}
2^{n} &\ge n + 1 \\
&> n.
\end{align*}
Put $m = 2k+1$. Then $m^2 \equiv 1 \pmod{8}$ and $2^5 = 31$. Also $n^2 \ge 2n + 4$.
"""
res = C.check_passage(stmt, proof, GL)
by = {r["tex"].replace(" ", ""): r for r in res}
st = lambda tex: by[tex.replace(" ", "")]["status"]  # noqa: E731
assert st(r"(a+b)^2 = a^2+2ab+b^2") == "proved"
assert st(r"a^2+2ab+b^2 \le 2a^2+2b^2") == "exhaustive"
assert st(r"\binom {n}{2} = \frac {n(n-1)}{2}") == "proved"
bad = by[r"\binom{n}{2}+n=\frac{n(n+1)}{3}"]
assert bad["status"] == "counterexample" and "n = 1" in bad["detail"], bad
assert st(r"|G|^2 \le 4^k") == "follows"                       # from the line before it
assert st(r"|G| = 3s+1") == "given"                            # justified by a lemma and introduces s
assert st(r"|G|^2 \le 2^k") == "unsupported"                   # abstract: never called false, left to the proof check
assert st(r"2^{n} \ge n+1") == "exhaustive" and st(r"n+1 > n") == "proved"   # the aligned line continues the one before
assert st(r"m^2 \equiv 1 \pmod{8}") == "follows"               # m = 2k+1 substituted
assert st(r"2^5 = 31") == "counterexample"
assert st(r"n^2 \ge 2n+4") == "counterexample"
note = C.for_model(res)
assert "VERIFIED" in note and "FAILS" in note and "NOT IMPLIED" in note

# the model only translates; its translation is read by a small reader (no eval) and checked against the formula
ctx2 = C._Ctx(GL)
step = C.from_translation({"lhs": "order_G^2", "rel": "<=", "rhs": "4^k", "symbols": {"order_G": {"type": "positive integer", "means": "|G|"},
                           "k": {"type": "positive integer", "means": "k"}}}, ctx2, r"|G|^2 \le 4^k")
assert isinstance(step, C.Step)
assert isinstance(C.from_translation({"lhs": "x", "rel": "=", "rhs": "2", "symbols": {"x": "positive integer"}}, ctx2, r"x = 3"), str)
for evil in ("__import__('os').system('x')", "x.__class__", "lambda: 1"):
    try:
        C.read_plain(evil, {"x": ctx2.sym("x")})
        raise AssertionError(evil)
    except C.Unreadable:
        pass
called = []
def fake(items, c):
    called.extend(items)
    return {it["id"]: C.from_translation({"lhs": "1 + 2 + 3", "rel": "=", "rhs": "6", "symbols": {}}, c, it["tex"]) for it in items}
out = C.check_passage("", r"Hence $1 + 2 + \cdots + 3 = 6$.", [], translate=fake)
assert called and out[0]["status"] == "proved" and out[0].get("translated"), out
print("compute ok")
