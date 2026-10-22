# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Computer checks of proofs.

A proof is cut into steps by code: every relation in a formula is one step (A = B <= C is two steps, and a line that
begins with a relation continues the line before). A step is checked on this computer when it is pure computation:
every part of it can be read as exact mathematics and every symbol has a type, taken from the notation the author
confirmed. Abstract quantities (|G|, i(G)) become opaque numbers of their type, so the arithmetic around them is still
checked. Everything else is a logical step and is left to the proof check, which is told what was verified.

A model may be asked to translate a formula the code cannot read, never to judge it; its translation is compared with
the formula's own numbers and symbols, and a step checked from a translation says so.

Checks, strongest first: an exact proof (SymPy); every value in a range, for integer symbols; values tested in exact or
60-digit arithmetic; and, when a step fails on its own, the same test restricted to values that satisfy the lines
before it in the proof. A counterexample is always the smallest found and is evaluated again exactly."""
from __future__ import annotations

import itertools
import json
import random
import re

try:
    import mpmath
    import sympy as sp
except ImportError:          # SymPy is in requirements.txt; without it the proof check simply runs as before
    sp = mpmath = None

AVAILABLE = sp is not None

# ----------------------------------------------------------------------------- reading LaTeX

_GREEK = set("alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu xi rho sigma tau "
             "upsilon phi varphi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega".split())
_REL = {"=": "=", "<": "<", ">": ">", "\\le": "<=", "\\leq": "<=", "\\leqslant": "<=", "\\ge": ">=", "\\geq": ">=",
        "\\geqslant": ">=", "\\ne": "!=", "\\neq": "!=", "\\equiv": "≡", "\\mid": "|", "\\nmid": "∤", "\\lt": "<", "\\gt": ">"}
_SHOW = {"<=": "\\le", ">=": "\\ge", "!=": "\\ne", "≡": "\\equiv", "|": "\\mid", "∤": "\\nmid", "=": "=", "<": "<", ">": ">"}
_OTHER = {"\\approx", "\\sim", "\\simeq", "\\cong", "\\to", "\\rightarrow", "\\mapsto", "\\in", "\\notin", "\\subset",
          "\\subseteq", "\\supset", "\\supseteq", "\\subsetneq", "\\ll", "\\gg", "\\propto", "\\perp", ":=", "\\coloneqq",
          "\\lhd", "\\unlhd", "\\trianglelefteq", "\\triangleleft", "\\leftarrow", "\\longrightarrow",
          "\\hookrightarrow", "\\twoheadrightarrow", "\\models", "\\vdash", "\\nsubseteq"}
_SEP = {"\\implies", "\\Rightarrow", "\\Longrightarrow", "\\therefore", "\\iff", "\\Leftrightarrow", "\\Longleftrightarrow",
        "\\impliedby", "\\Leftarrow", "\\Longleftarrow", ",", ";", "\\quad", "\\qquad", "\\text", "\\mbox", "\\textrm",
        "\\textup", "\\forall", "\\exists", "\\land", "\\lor", "\\wedge", "\\vee"}
_DROP = {"\\,", "\\;", "\\:", "\\!", "\\ ", "~", "\\displaystyle", "\\textstyle", "\\scriptstyle", "\\nonumber", "\\notag",
         "&", "\\limits", "\\nolimits", "\\phantom", "\\hfill", "\\allowbreak", "\\\\", "\\cr"}
_SIZES = {"\\left", "\\right", "\\big", "\\Big", "\\bigg", "\\Bigg", "\\bigl", "\\bigr", "\\Bigl", "\\Bigr", "\\biggl",
          "\\biggr", "\\Biggl", "\\Biggr", "\\middle"}
_FUNCS = {"\\log": "log", "\\ln": "log", "\\exp": "exp", "\\sin": "sin", "\\cos": "cos", "\\tan": "tan",
          "\\gcd": "gcd", "\\max": "max", "\\min": "min", "\\lg": "log2"}
_NAMED = {"gcd", "lcm", "log", "ln", "exp", "max", "min", "sin", "cos", "tan", "deg"}
_TOKEN = re.compile(r"\\[A-Za-z]+\*?|\\.|:=|\d+(?:\.\d+)?|[A-Za-z]|'|\S")
_HYP = re.compile(r"(?:\blet|\bset|\bput|\bdefine|\bdenote|\bwrite|\bwhere|\bwith|\bsuppose|\bassume|\bif|\bgiven|\bfor|"
                  r"\bwhenever|\bsince|\bbecause|\bas|\bthat is|\bi\.e\.|\bcall|\bover|\bsumming|\branging|\bruns?|\brunning|"
                  r"\beach|\bevery|\ball|\bcases?|\bwhen|\bat|\bfrom|\bindex)\W*(?:\w+\W+){0,3}$", re.I)


class Unreadable(Exception):
    """The formula is not pure computation, or not in a form this reader knows."""


def _tokens(tex: str) -> list[str]:
    tex = re.sub(r"\\(label|tag|ref|eqref|cite)\*?\{[^{}]*\}", " ", tex)
    tex = re.sub(r"\\(color|textcolor)\{[^{}]*\}", " ", tex)
    toks, out = _TOKEN.findall(tex), []
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in _SIZES:
            nxt = toks[i + 1] if i + 1 < len(toks) else ""
            i += 2
            if nxt in ("(", "[", ")", "]", "|", "\\lfloor", "\\rfloor", "\\lceil", "\\rceil", "\\vert", "\\{", "\\}", "\\langle", "\\rangle", "\\|"):
                out.append({"\\vert": "|"}.get(nxt, nxt))
            continue
        if t in ("\\overset", "\\stackrel", "\\underset"):          # a label written over a relation: the relation stays
            j, depth = i + 1, 0
            while j < len(toks):                                      # skip the first group
                depth += toks[j] == "{"
                depth -= toks[j] == "}"
                j += 1
                if depth == 0:
                    break
            if j + 2 < len(toks) and toks[j] == "{" and toks[j + 2] == "}" and (toks[j + 1] in _REL or toks[j + 1] in _OTHER):
                out.append(toks[j + 1])
                i = j + 3
                continue
        if t not in _DROP:
            out.append({"\\vert": "|", "\\lvert": "|", "\\rvert": "|", "\\cdotp": "\\cdot", "\\ast": "*"}.get(t, t))
        i += 1
    while out and out[-1] in (".", ",", ";"):
        out.pop()
    return out


def _statements(toks: list[str]) -> list[list[str]]:
    """Split a formula at top-level separators (commas, \\implies, words) into statements."""
    parts, cur, depth, abs_open = [], [], 0, False
    i = 0
    while i < len(toks):
        t = toks[i]
        depth += t in ("{", "(", "[", "\\lfloor", "\\lceil")
        depth -= t in ("}", ")", "]", "\\rfloor", "\\rceil")
        if depth == 0 and t in _SEP:
            parts.append(cur)
            cur = []
            if t in ("\\text", "\\mbox", "\\textrm", "\\textup") and i + 1 < len(toks) and toks[i + 1] == "{":
                d, i = 0, i + 1
                while i < len(toks):
                    d += toks[i] == "{"
                    d -= toks[i] == "}"
                    if d == 0:
                        break
                    i += 1
            i += 1
            continue
        cur.append(t)
        i += 1
    parts.append(cur)
    return [p for p in parts if p]


def _chain(toks: list[str]) -> list:
    """[expr_tokens, rel, expr_tokens, rel, ...]; rel is a key of _REL values or 'other'."""
    out, cur, depth, bars = [], [], 0, 0
    for t in toks:
        depth += t in ("{", "(", "[", "\\lfloor", "\\lceil")
        depth -= t in ("}", ")", "]", "\\rfloor", "\\rceil")
        if depth == 0 and (t in _REL or t in _OTHER) and not (t == "|" and False):
            out += [cur, _REL.get(t, "other")]
            cur = []
        else:
            cur.append(t)
    out.append(cur)
    return out


class _Ctx:
    """Symbols and their types for one paper."""

    def __init__(self, glossary: list[dict] | None = None):
        self.meaning: dict[str, str] = {}
        self.functions: set[str] = {"f", "g", "h", "F"}
        for g in glossary or []:
            s = re.sub(r"[\s$]", "", str(g.get("symbol") or ""))
            if not s:
                continue
            self.meaning[s] = str(g.get("meaning") or "")
            m = re.match(r"^\\?([A-Za-z]+)\(", s)
            if m:
                self.functions.add(m.group(1))
        self.symbols: dict[str, tuple] = {}
        self.translated = False

    def type_of(self, name: str) -> str:
        words = self.meaning.get(name) or self.meaning.get(name.replace("\\", "")) or ""
        t = type_from_words(words)
        if t:
            return t
        if name.startswith("|"):
            return "pos_int"
        if re.match(r"^\\?[A-Za-z]+\(", name):
            return "nonneg_int"
        base = name.lstrip("\\").split("_")[0].rstrip("'")
        if re.fullmatch(r"[A-Z]", base) or base in ("Gamma", "Delta", "Theta", "Lambda", "Xi", "Sigma", "Upsilon", "Phi", "Psi", "Omega"):
            return "object"
        if base in ("x", "y", "z", "u", "v", "w", "t", "epsilon", "varepsilon", "delta", "theta", "xi", "eta"):
            return "pos_real"
        return "pos_int"

    def sym(self, name: str):
        if name not in self.symbols:
            typ = self.type_of(name)
            if typ == "object":
                raise Unreadable(f"{name} is not a number")
            kw = {"pos_int": dict(integer=True, positive=True), "nonneg_int": dict(integer=True, nonnegative=True),
                  "int": dict(integer=True), "prime": dict(integer=True, positive=True, prime=True),
                  "odd_prime": dict(integer=True, positive=True, prime=True, odd=True),
                  "pos_real": dict(positive=True), "real": dict(real=True)}[typ]
            safe = "v" + str(len(self.symbols)) + "_" + re.sub(r"\W", "", name)[:12]
            self.symbols[name] = (sp.Symbol(safe, **kw), typ)
        return self.symbols[name][0]

    def name_of(self, s) -> str:
        return next((n for n, (x, _) in self.symbols.items() if x == s), str(s))


_PROSE_TYPES = [
    (re.compile(r"\b(odd prime|odd primes)\s+\$([^$]{1,20})\$"), "an odd prime"),
    (re.compile(r"\$([^$]{1,20})\$\s+(?:is|be|are)\s+(?:an?\s+)?odd primes?\b"), "an odd prime"),
    (re.compile(r"\b(?<!odd )(prime|primes)\s+\$([^$]{1,20})\$"), "a prime"),
    (re.compile(r"\$([^$]{1,20})\$\s+(?:is|be|are)\s+(?:an?\s+)?(?:distinct\s+)?primes?\b"), "a prime"),
    (re.compile(r"\b(positive integers?|natural numbers?)\s+\$([^$]{1,20})\$"), "a positive integer"),
    (re.compile(r"\$([^$]{1,20})\$\s+(?:is|be|are)\s+(?:an?\s+)?(?:positive integers?|natural numbers?)\b"), "a positive integer"),
    (re.compile(r"\b(integers?)\s+\$([^$]{1,20})\$"), "an integer"),
    (re.compile(r"\b(real numbers?|reals?)\s+\$([^$]{1,20})\$"), "a real number"),
]


def types_from_prose(text: str) -> dict[str, str]:
    """'$p$ is an odd prime', 'for every prime $q$', '$x \\in \\mathbb{R}$': the types the text states."""
    out: dict[str, str] = {}
    for rx, meaning in _PROSE_TYPES:
        for m in rx.finditer(text or ""):
            inner = m.group(m.lastindex)
            for name in re.split(r"\s*,\s*|\s+and\s+", inner):
                lead = re.match(r"\s*(\\?[A-Za-z]+(?:_\{?\w+\}?)?)", name)
                if lead:
                    out.setdefault(lead.group(1), meaning)
    for m in re.finditer(r"\$([^$]{1,40}?)\\in\s*\\mathbb\{([NZR])\}", text or ""):
        meaning = {"N": "a positive integer", "Z": "an integer", "R": "a real number"}[m.group(2)]
        for name in re.split(r"\s*,\s*", m.group(1)):
            name = re.sub(r"\s", "", name)
            if re.fullmatch(r"\\?[A-Za-z]+(_\{?\w+\}?)?", name):
                out.setdefault(name, meaning)
    return out


def type_from_words(meaning: str) -> str | None:
    m = (meaning or "").lower()
    if not m:
        return None
    if re.match(r"^\W*(an?\s+|the\s+)?(odd\s+)?prime\b", m) or (
            re.search(r"\bprimes?\b", m) and not re.search(r"\b(power|composite|order|number of|counting)\b", m)):
        return "odd_prime" if re.search(r"\bodd\b", m) else "prime"
    if re.search(r"\b(positive integer|natural number|integer \S* ?(≥|>=|\\ge) ?1)\b", m):
        return "pos_int"
    if re.search(r"\b(number of|order of|cardinality|how many|count of|index of|size of|exponent)\b", m):
        return "nonneg_int"
    if re.search(r"\b(non-?negative integer)\b", m):
        return "nonneg_int"
    if re.search(r"\binteger\b", m):
        return "int"
    if re.search(r"\b(positive real|positive constant|small constant)\b", m):
        return "pos_real"
    if re.search(r"\b(real|constant)\b", m):
        return "real"
    if re.search(r"\b(group|subgroup|set|field|ring|module|space|graph|map|function|homomorphism|automorphism|element|matrix|vector|"
                 r"permutation|ideal|category|operator|polynomial)\b", m):
        return "object"
    return None


class _Reader:
    def __init__(self, toks: list[str], ctx: _Ctx):
        self.t, self.i, self.ctx, self.bars = toks, 0, ctx, 0
        self.raw: list[str] = []

    def peek(self, k: int = 0):
        j = self.i + k
        return self.t[j] if j < len(self.t) else None

    def take(self):
        tok = self.peek()
        if tok is None:
            raise Unreadable("the formula ends too early")
        self.i += 1
        return tok

    def expect(self, tok: str):
        if self.take() != tok:
            raise Unreadable(f"expected {tok}")

    def whole(self):
        if not self.t:
            raise Unreadable("empty")
        e = self.expr()
        if self.peek() is not None:
            raise Unreadable(f"cannot read {self.peek()}")
        return e

    def group_text(self) -> str:
        if self.peek() != "{":
            return self.take()
        self.take()
        depth, parts = 1, []
        while True:
            t = self.take()
            depth += t == "{"
            depth -= t == "}"
            if depth == 0:
                return "".join(parts)
            parts.append(t)

    def group(self):
        if self.peek() == "{":
            self.take()
            e = self.expr()
            self.expect("}")
            return e
        t = self.peek()
        if t and re.fullmatch(r"\d{2,}", t):        # x^23 is x^2 3 in TeX
            self.t[self.i] = t[1:]
            return sp.Integer(int(t[0]))
        return self.atom()

    def starts_factor(self, t) -> bool:
        if t is None:
            return False
        if t == "|":
            return self.bars == 0
        return bool(re.fullmatch(r"\d.*|[A-Za-z]", t)) or t in ("(", "[", "\\lfloor", "\\lceil", "{") or (
            t.startswith("\\") and t not in ("\\cdot", "\\times", "\\div", "\\rfloor", "\\rceil", "\\right") and t not in _REL and t not in _OTHER)

    def expr(self):
        if self.peek() in ("-", "+"):
            sign = -1 if self.take() == "-" else 1
            e = sign * self.term()
        else:
            e = self.term()
        while self.peek() in ("+", "-", "\\pm", "\\mp"):
            op = self.take()
            if op in ("\\pm", "\\mp"):
                raise Unreadable("± is two formulas")
            e = e + self.term() if op == "+" else e - self.term()
        return e

    def term(self):
        e = self.power()
        while True:
            t = self.peek()
            if t in ("\\cdot", "\\times", "*"):
                self.take()
                e = e * self.power()
            elif t in ("/", "\\div"):
                self.take()
                e = e / self.power()
            elif self.starts_factor(t):
                e = e * self.power()
            else:
                return e

    def power(self):
        if self.peek() == "-":
            self.take()
            return -self.power()
        base = self.atom()
        while True:
            if self.peek() == "!":
                self.take()
                base = sp.factorial(base)
            elif self.peek() == "^":
                self.take()
                if self.peek() in ("\\prime", "'"):
                    raise Unreadable("a derivative")
                base = base ** self.group()
            else:
                return base

    def args(self) -> list:
        self.expect("(")
        out = [self.expr()]
        while self.peek() == ",":
            self.take()
            out.append(self.expr())
        self.expect(")")
        return out

    def opaque_call(self, name: str):
        """f(args) for a function the reader does not know: an opaque number named by its text, like i(G)."""
        depth, parts = 0, []
        while self.peek() is not None:
            t = self.take()
            depth += t == "("
            depth -= t == ")"
            parts.append(t)
            if depth == 0:
                break
        return self.ctx.sym(name + "".join(parts))

    def name(self, first: str) -> str:
        name = first
        while self.peek() == "'":
            self.take()
            name += "'"
        if self.peek() == "_":
            self.take()
            name += "_" + self.group_text()
        return name

    def apply(self, fname: str, args: list):
        f = {"log": sp.log, "exp": sp.exp, "sin": sp.sin, "cos": sp.cos, "tan": sp.tan, "max": sp.Max, "min": sp.Min,
             "log2": lambda x: sp.log(x, 2)}.get(fname)
        if fname in ("gcd", "lcm"):
            return (IGcd if fname == "gcd" else ILcm)(*args)
        if f is None:
            raise Unreadable(f"the function {fname}")
        return f(*args)

    def known_function(self, name: str) -> str | None:
        words = (self.ctx.meaning.get(name) or self.ctx.meaning.get(name + "(n)") or "").lower()
        if "totient" in words or (name in ("\\phi", "\\varphi") and "euler" in words):
            return "totient"
        if "sum of" in words and "divisor" in words:
            return "sigma"
        if "number of" in words and "divisor" in words:
            return "tau"
        if "möbius" in words or "mobius" in words:
            return "mobius"
        if "prime" in words and ("number of" in words or "counting" in words) and "divisor" not in words:
            return "primepi"
        return None

    def atom(self):
        t = self.take()
        if re.fullmatch(r"\d+(\.\d+)?", t):
            return sp.Rational(t)
        if t == "{":
            e = self.expr()
            self.expect("}")
            return e
        if t in ("(", "["):
            e = self.expr()
            self.expect(")" if t == "(" else "]")
            return e
        if t == "|":
            j, depth = self.i, 0
            while j < len(self.t) and not (self.t[j] == "|" and depth == 0):
                depth += self.t[j] in ("{", "(", "[")
                depth -= self.t[j] in ("}", ")", "]")
                j += 1
            if j >= len(self.t):
                raise Unreadable("an unclosed |")
            inner = self.t[self.i:j]
            try:
                e = _Reader(list(inner), self.ctx).whole()
            except Unreadable as why:
                if "not a number" not in str(why):
                    raise
                self.i = j + 1
                return self.ctx.sym("|" + "".join(inner) + "|")
            self.i = j + 1
            return sp.Abs(e)
        if re.fullmatch(r"[A-Za-z]", t) or (t.startswith("\\") and t[1:] in _GREEK) or (t == "\\pi" and self.peek() == "("):
            nm = self.name(t)
            base = t.lstrip("\\")
            if self.peek() == "(" and (base in self.ctx.functions or t in ("\\phi", "\\varphi", "\\psi", "\\sigma", "\\tau", "\\mu", "\\lambda", "\\pi", "\\omega", "\\Omega")):
                known = self.known_function(t) or self.known_function(base)
                if known:
                    a = self.args()
                    return {"totient": sp.totient, "sigma": sp.divisor_sigma, "tau": sp.divisor_count, "primepi": sp.primepi,
                            "mobius": getattr(sp, "mobius", None) or _no("Möbius")}[known](*a)
                return self.opaque_call(nm)
            if t == "e" and self.peek() == "^" and "e" not in self.ctx.meaning:
                return sp.E
            return self.ctx.sym(nm)
        if t in ("\\frac", "\\dfrac", "\\tfrac", "\\cfrac"):
            a = self.group()
            return a / self.group()
        if t in ("\\binom", "\\dbinom", "\\tbinom"):
            a = self.group()
            return sp.binomial(a, self.group())
        if t == "\\sqrt":
            n = 2
            if self.peek() == "[":
                self.take()
                n = self.expr()
                self.expect("]")
            return sp.root(self.group(), n)
        if t in ("\\lfloor", "\\lceil"):
            e = self.expr()
            self.expect("\\rfloor" if t == "\\lfloor" else "\\rceil")
            return sp.floor(e) if t == "\\lfloor" else sp.ceiling(e)
        if t in ("\\cancel", "\\bcancel", "\\xcancel"):
            e = self.group()                 # \cancel{4}^2: 4 cancelled down to 2 by hand; the value is the original one
            while self.peek() in ("^", "_"):
                self.take()
                self.group_text()
            return e
        if t in ("\\boxed", "\\mathbf", "\\bm", "\\boldsymbol"):
            return self.group()
        if t in ("\\underbrace", "\\overbrace", "\\underline", "\\overline"):
            if t == "\\overline":
                raise Unreadable("a bar over a symbol")
            e = self.group()
            if self.peek() in ("_", "^") and t in ("\\underbrace", "\\overbrace"):
                self.take()
                self.group_text()
            return e
        if t in ("\\underset", "\\overset"):
            self.group_text()
            return self.group()
        if t in _FUNCS or t in ("\\operatorname", "\\mathrm", "\\operatorname*"):
            fname = _FUNCS.get(t) or self.group_text()
            if fname not in _NAMED and fname not in _FUNCS.values():
                if self.peek() == "(":
                    return self.opaque_call(fname)
                raise Unreadable(f"the word {fname}")
            fname = {"ln": "log"}.get(fname, fname)
            base = None
            if self.peek() == "_" and fname == "log":
                self.take()
                base = self.group()
            if self.peek() == "^":
                raise Unreadable("a power of a function name")
            arg = self.args() if self.peek() == "(" else [self.power()]
            if base is not None:
                return sp.log(arg[0], base)
            return self.apply(fname, arg)
        if t in ("\\sum", "\\prod"):
            if self.peek() != "_":
                raise Unreadable("a sum without limits")
            self.take()
            sub = self.group_text()
            m = re.fullmatch(r"([A-Za-z])=(.+)", sub)
            if not m or self.peek() != "^":
                raise Unreadable("a sum over a set")
            self.take()
            var = self.ctx.sym(m.group(1))
            lo = _Reader(_tokens(m.group(2)), self.ctx).whole()
            hi = self.group()
            body = self.term()
            return (sp.Sum if t == "\\sum" else sp.Product)(body, (var, lo, hi))
        if t == "\\pi" and "\\pi" not in self.ctx.meaning and self.peek() != "(":
            return sp.pi
        if t == "\\infty":
            raise Unreadable("infinity")
        if t in ("\\dots", "\\cdots", "\\ldots", "\\dotsb", "\\dotsc", "\\vdots"):
            raise Unreadable("a sum written with dots")
        if t == "\\mathbb" or t == "\\{":
            raise Unreadable("a set")
        raise Unreadable(f"{t}")


def _no(what: str):
    raise Unreadable(what)


if sp is not None:
    class IGcd(sp.Function):
        @classmethod
        def eval(cls, a, b):
            if a.is_Integer and b.is_Integer:
                return sp.Integer(sp.igcd(int(a), int(b)))

    class ILcm(sp.Function):
        @classmethod
        def eval(cls, a, b):
            if a.is_Integer and b.is_Integer:
                return sp.Integer(sp.ilcm(int(a), int(b)))


def read(tex: str, ctx: _Ctx):
    return _Reader(_tokens(tex), ctx).whole()


# ----------------------------------------------------------------------------- cutting a proof into steps

_MATH = re.compile(r"\\\[(?P<a>.+?)\\\]|\$\$(?P<b>.+?)\$\$|\\begin\{(?P<env>equation|align|gather|multline|eqnarray|flalign)(?P<st>\*?)\}"
                   r"(?P<c>.+?)\\end\{(?P=env)(?P=st)\}|\\\((?P<d>.+?)\\\)|(?<!\\)\$(?P<e>[^$]+?)\$", re.S)
_INNER = re.compile(r"\\(begin|end)\{(split|aligned|alignedat|gathered|multlined|cases)\}(\{[^{}]*\})?")


def formulas(text: str) -> list[dict]:
    """Each formula line of a passage with the words just before it."""
    out, last = [], 0
    for m in _MATH.finditer(text or ""):
        before = re.sub(r"\s+", " ", text[last:m.start()])
        before = re.split(r"(?<=[.!?])\s", before)[-1][-160:]
        last = m.end()
        body = next(g for g in (m.group("a"), m.group("b"), m.group("c"), m.group("d"), m.group("e")) if g is not None)
        display = m.group("e") is None and m.group("d") is None
        if "\\begin{cases}" in body:
            continue
        lines = re.split(r"\\\\(?:\[[^\]]*\])?", _INNER.sub(" ", body))
        for k, line in enumerate(lines):
            if line.strip():
                out.append({"tex": line.strip(), "before": before if k == 0 else "", "display": display, "first": k == 0})
    return out


_REASON = re.compile(r"\b(by|since|because|from|using|applying|lemma|theorem|proposition|corollary|definition|hypothesis|"
                     r"assumption|assume|induction|claim|recall|known|previous|above)\b|\\(ref|eqref|cite)|\(\d+\)", re.I)


def steps_of(text: str) -> list[dict]:
    """Every relation in a passage as a step {lhs, rel, rhs, tex, claim, reason}; a formula without a relation is
    a mention {rel: None, lhs} (its symbols count as introduced)."""
    steps, prev_rhs = [], None
    for f in formulas(text):
        hyp = bool(_HYP.search(f["before"])) or ":=" in f["tex"] or "\\coloneqq" in f["tex"]
        reason = bool(_REASON.search(f["before"]))
        for st in _statements(_tokens(f["tex"])):
            ch = _chain(st)
            if not ch[0] and len(ch) > 1 and prev_rhs is not None:
                ch[0] = prev_rhs
            if len(ch) == 1:
                steps.append({"rel": None, "lhs": ch[0]})
                continue
            for j in range(1, len(ch) - 1, 2):
                lhs, rel, rhs = ch[j - 1], ch[j], ch[j + 1]
                if not lhs or not rhs:
                    continue
                mod = None
                for k, t in enumerate(rhs):
                    if t in ("\\pmod", "\\bmod", "\\mod") and rel == "≡":
                        rhs, mod = rhs[:k], rhs[k + 1:]
                        if rhs and rhs[-1] == "(":
                            rhs, mod = rhs[:-1], mod[:-1] if mod and mod[-1] == ")" else mod
                        break
                steps.append({"lhs": lhs, "rel": rel, "rhs": rhs, "mod": mod, "claim": not hyp, "reason": reason,
                              "tex": f"{''.join(_join(lhs))} {_SHOW.get(rel, '?')} {''.join(_join(rhs))}"
                                     + (f" \\pmod{{{''.join(_join(mod)).strip('{}')}}}" if mod else "")})
            if ch[-1]:
                prev_rhs = ch[-1]
    return steps


def _join(toks: list[str]) -> list[str]:
    out = []
    for t in toks:
        out.append(t + (" " if re.fullmatch(r"\\[A-Za-z]+", t) else ""))
    return out


# ----------------------------------------------------------------------------- checking

_PRIMES = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67, 71, 73, 79, 83, 89, 97]
_REALS = ["1/100", "1/7", "1/2", "1", "3/2", "2", "7/3", "5", "10", "100"]
_BUDGET = 4000
_HUGE = 10 ** 45


class _Undefined(Exception):
    pass


def _values(typ: str, size: int) -> list:
    if typ in ("prime", "odd_prime"):
        ps = _PRIMES[1:] if typ == "odd_prime" else _PRIMES
        return [sp.Integer(p) for p in ps[:max(3, min(size, len(ps)))]]
    if typ in ("pos_int", "nonneg_int", "int"):
        lo = {"pos_int": 1, "nonneg_int": 0, "int": -size // 2}[typ]
        return [sp.Integer(v) for v in range(lo, lo + size)]
    vals = [sp.Rational(r) for r in _REALS]
    return vals + [-v for v in vals] + [sp.Integer(0)] if typ == "real" else vals


def _holds(rel: str, a, b, mod=None) -> bool:
    for v in (a, b):
        if isinstance(v, mpmath.mpc) and abs(v.imag) > mpmath.mpf(10) ** -30:
            raise _Undefined
    a, b = mpmath.re(a), mpmath.re(b)
    if not (mpmath.isfinite(a) and mpmath.isfinite(b)) or abs(a) > _HUGE or abs(b) > _HUGE:
        raise _Undefined
    tol = mpmath.mpf(10) ** -35 * max(1, abs(a), abs(b))
    d = a - b
    if rel == "=":
        return abs(d) <= tol
    if rel == "!=":
        return abs(d) > tol
    if rel == "<=":
        return d <= tol
    if rel == "<":
        return d < -tol
    if rel == ">=":
        return d >= -tol
    if rel == ">":
        return d > tol
    if rel in ("|", "∤"):
        if abs(a) <= tol or abs(a - mpmath.nint(a)) > tol or abs(b - mpmath.nint(b)) > tol:
            raise _Undefined
        q = b / a
        return (abs(q - mpmath.nint(q)) <= tol) == (rel == "|")
    raise _Undefined


_MODS = None


def _mods() -> dict:
    global _MODS
    if _MODS is None:
        def _int(x):
            v = mpmath.nint(x)
            if abs(x - v) > mpmath.mpf(10) ** -30:
                raise _Undefined
            return int(v)
        _MODS = {"totient": lambda n: sp.totient(_int(n)) if _int(n) > 0 else _undef(),
                 "divisor_sigma": lambda n, k=1: int(sp.divisor_sigma(_int(n), k)),
                 "divisor_count": lambda n: int(sp.divisor_count(_int(n))),
                 "IGcd": lambda a, b: sp.igcd(_int(a), _int(b)), "ILcm": lambda a, b: sp.ilcm(_int(a), _int(b)),
                 "floor": mpmath.floor, "ceiling": mpmath.ceil, "factorial": lambda n: mpmath.factorial(_int(n)) if _int(n) >= 0 else _undef(),
                 "binomial": mpmath.binomial, "primepi": lambda n: int(sp.primepi(_int(n))), "mobius": lambda n: int(sp.mobius(_int(n))) if hasattr(sp, "mobius") else _undef()}
    return _MODS


def _undef():
    raise _Undefined


def _fn(vars_, expr):
    expr = expr.doit() if expr.has(sp.Sum, sp.Product) else expr
    return sp.lambdify(vars_, expr, modules=[_mods(), "mpmath"])


def _num(e):
    e = sp.nsimplify(e) if e.is_Float else e
    if e.is_Rational:
        return str(e)
    return str(sp.N(e, 12))


class Step:
    def __init__(self, lhs, rel, rhs, tex: str):
        self.lhs, self.rel, self.rhs, self.tex = lhs, rel, rhs, tex

    @property
    def free(self) -> set:
        return set(self.lhs.free_symbols | self.rhs.free_symbols)


def _substitute(step: Step, hyps: list[Step]) -> tuple[Step, list[Step], dict]:
    """Lines of the form x = expression are used by substituting them, not by searching for values that satisfy them."""
    subs, rest = {}, []
    for h in hyps:
        for a, b in ((h.lhs, h.rhs), (h.rhs, h.lhs)):
            if h.rel == "=" and a.is_Symbol and b.free_symbols and a not in b.free_symbols and a not in subs and \
                    not any(a in v.free_symbols for v in subs.values()):
                subs[a] = b
                break
        else:
            rest.append(h)
    if not subs:
        return step, hyps, {}
    for _ in range(3):
        subs = {k: v.subs(subs) for k, v in subs.items()}
    put = lambda e: e.subs(subs)  # noqa: E731
    return (Step(put(step.lhs), step.rel, put(step.rhs), step.tex),
            [Step(put(h.lhs), h.rel, put(h.rhs), h.tex) for h in rest], subs)


def _search(step: Step, ctx: _Ctx, hyps: list[Step], budget: int = _BUDGET):
    """(status, detail, counterexample). status: proved | exhaustive | tested | fails | vacuous"""
    step, hyps, subs = _substitute(step, hyps)
    if subs and not hyps and _proved(step):
        return "proved", "exact algebra after substituting the lines before it", None
    if subs and not step.free:
        ok = check_step(step, ctx, [])
        if ok["status"] == "proved":
            return "exhaustive", "exact computation after substituting the lines before it", None
        return ("fails", ok["detail"] + " after substituting the lines before it", {}) if ok["status"] == "counterexample" else ("vacuous", "", None)
    syms = sorted(step.free | {s for h in hyps for s in h.free}, key=str)
    types = {s: next((t for _, (x, t) in ctx.symbols.items() if x == s), "pos_int") for s in syms}
    ints = all(t in ("pos_int", "nonneg_int", "int", "prime", "odd_prime") for t in types.values())
    fs = _fn(syms, step.lhs), _fn(syms, step.rhs)
    hfs = [(_fn(syms, h.lhs), h.rel, _fn(syms, h.rhs)) for h in hyps]
    n = max(1, len(syms))
    size = max(3, min(40, int(round(budget ** (1 / n)))))
    domains = [_values(types[s], size) for s in syms]
    total = 1
    for d in domains:
        total *= len(d)
    points = itertools.product(*domains) if total <= budget * 1.5 else (
        tuple(random.Random(7 + k).choice(d) for d in domains) for k in range(budget))
    tried = satisfied = 0
    with mpmath.workdps(60):
        for pt in sorted(points, key=lambda p: sum(abs(int(v)) if v.is_Integer else 0 for v in p)) if total <= budget * 1.5 else points:
            tried += 1
            args = [mpmath.mpf(int(v.p)) / int(v.q) for v in pt]
            try:
                if any(not _holds(r, hl(*args), hr(*args)) for hl, r, hr in hfs):
                    continue
                satisfied += 1
                if not _holds(step.rel, fs[0](*args), fs[1](*args)):
                    cx = dict(zip(syms, pt))
                    if _confirm(step, cx):
                        cx.update({k: v.subs(cx) for k, v in subs.items()})
                        return "fails", "", cx
            except (_Undefined, ZeroDivisionError, ValueError, OverflowError, TypeError, ArithmeticError):
                continue
    if satisfied == 0:
        return "vacuous", "", None
    ranges = ", ".join(f"{ctx.name_of(s).lstrip(chr(92))} ≤ {max(int(v) for v in d)}" for s, d in zip(syms, domains)
                       if "prime" not in types[s]) + ("" if all("prime" not in types[s] for s in syms) else
                                                       (", " if any("prime" not in types[s] for s in syms) else "") + "the first primes")
    if ints and total <= budget * 1.5:
        return "exhaustive", f"every case with {ranges}" + (f" ({satisfied} satisfying the earlier lines)" if hyps else ""), None
    return "tested", f"{satisfied} sets of values" + (" satisfying the earlier lines" if hyps else ""), None


def _confirm(step: Step, cx: dict) -> bool:
    """A counterexample found in 60-digit arithmetic is evaluated again exactly."""
    try:
        a, b = step.lhs.subs(cx).doit(), step.rhs.subs(cx).doit()
        with mpmath.workdps(90):
            return not _holds(step.rel, mpmath.mpmathify(sp.N(a, 90)), mpmath.mpmathify(sp.N(b, 90)))
    except Exception:  # noqa: BLE001 - anything odd means: not a confirmed counterexample
        return False


def _proved(step: Step) -> bool:
    try:
        d = step.lhs - step.rhs
        if sp.count_ops(d) > 80:
            return False
        if step.rel == "=":
            return sp.simplify(sp.expand(d)) == 0
        if step.rel in ("<=", ">="):
            e = sp.simplify(d if step.rel == ">=" else -d)
            return bool(e.is_nonnegative)
        if step.rel in ("<", ">"):
            e = sp.simplify(d if step.rel == ">" else -d)
            return bool(e.is_positive)
    except Exception:  # noqa: BLE001
        return False
    return False


def check_step(step: Step, ctx: _Ctx, facts: list[Step], pure: bool = True) -> dict:
    """pure: the step should follow by computation alone (no reason given, nothing abstract in it). A step that is not
    pure and does not follow is 'unsupported' (its justification is for the proof check), never a counterexample."""
    if not step.free:
        try:
            ok = _holds(step.rel, mpmath.mpmathify(sp.N(step.lhs.doit(), 60)), mpmath.mpmathify(sp.N(step.rhs.doit(), 60)))
        except (_Undefined, TypeError, ValueError, ZeroDivisionError):
            return {"status": "unchecked", "detail": "a value is undefined"}
        if ok:
            return {"status": "proved", "detail": "exact computation"}
        return {"status": "counterexample", "detail": f"left side {_num(step.lhs.doit())}, right side {_num(step.rhs.doit())}", "values": {}}
    if _proved(step):
        return {"status": "proved", "detail": "exact algebra for all values of the stated types"}
    status, detail, cx = _search(step, ctx, [])
    if status in ("exhaustive", "tested"):
        return {"status": status, "detail": detail}
    used = ""
    direct = [h for h in facts if h is not None and not _assignment(h) and h.free & step.free][-6:]
    trials = [[h] for h in reversed(direct)] + ([direct] if len(direct) > 1 else []) + [_near(step, facts)]
    failed = None
    for hs in trials:
        if not hs:
            continue
        status2, detail2, cx2 = _search(step, ctx, hs)
        if status2 in ("exhaustive", "tested", "proved"):
            return {"status": "follows", "detail": f"follows from the lines before it: {detail2}", "using": [h.tex for h in hs]}
        if status2 == "fails":
            failed = (hs, detail2, cx2)
    if failed is None and direct:
        return {"status": "unsupported", "detail": "no values satisfy the lines before it together with the stated types, so it "
                                                   "cannot be checked by computation"}
    if failed is not None:
        hs, detail2, cx2 = failed
        if cx2 == {}:
            return {"status": "counterexample" if pure else "unsupported", "detail": detail2, "values": {}}
        cx, used = cx2, " even using the lines before it"
    if cx is None:
        return {"status": "unchecked", "detail": "no values were found at which the expressions are defined"}
    a, b = step.lhs.subs(cx).doit(), step.rhs.subs(cx).doit()
    shown = ", ".join(f"{ctx.name_of(s).lstrip(chr(92))} = {v}" for s, v in sorted(cx.items(), key=lambda kv: ctx.name_of(kv[0])))
    kinds = sorted({t for s in cx for _, (x, t) in ctx.symbols.items() if x == s})
    where = f"at {shown}: left side {_num(a)}, right side {_num(b)}{used}; symbols taken as {', '.join(_KIND[k] for k in kinds)}"
    if not pure:
        return {"status": "unsupported", "values": {ctx.name_of(s): str(v) for s, v in cx.items()},
                "detail": f"does not follow by computation alone ({where}); its justification is for the proof check"}
    return {"status": "counterexample", "values": {ctx.name_of(s): str(v) for s, v in cx.items()}, "detail": where}


_KIND = {"pos_int": "positive integers", "nonneg_int": "non-negative integers", "int": "integers", "prime": "primes", "odd_prime": "odd primes",
         "pos_real": "positive reals", "real": "reals"}


def _assignment(h: Step) -> bool:
    return h.rel == "=" and any(a.is_Symbol and not b.free_symbols for a, b in ((h.lhs, h.rhs), (h.rhs, h.lhs)))


def _near(step: Step, facts: list[Step]) -> list[Step]:
    """The earlier lines that bear on a step: those sharing a symbol with it, and those sharing one with them."""
    usable = [h for h in facts if h is not None and not _assignment(h)]
    syms, chosen = set(step.free), []
    for _ in range(2):
        for h in reversed(usable):
            if h not in chosen and h.free & syms and len(h.free - syms) <= 2:
                chosen.append(h)
        syms = syms.union(*(h.free for h in chosen)) if chosen else syms
    return sorted(chosen, key=usable.index)[-10:]


def _make(s: dict, ctx: _Ctx) -> Step:
    lhs, rhs = _Reader(list(s["lhs"]), ctx).whole(), _Reader(list(s["rhs"]), ctx).whole()
    if s.get("mod"):
        m = _Reader(list(s["mod"]), ctx).whole()
        return Step(m, "|", lhs - rhs, s["tex"])
    return Step(lhs, s["rel"], rhs, s["tex"])


def _opaque(st: Step, ctx: _Ctx) -> tuple[bool, str]:
    names = [ctx.name_of(x) for x in st.free]
    calls = [re.match(r"^\\?([A-Za-z]+)\(", n) for n in names]
    heads = [m.group(1) for m in calls if m]
    if len(heads) != len(set(heads)):
        return True, "it depends on what the function is"
    return any(n.startswith("|") or m for n, m in zip(names, calls)), ""


def check_passage(statement: str, proof: str, glossary: list[dict] | None = None, translate=None) -> list[dict]:
    """Every relation in a proof, checked by computation where it is computation. translate(items, ctx) -> {id: Step
    or reason} is called once for the formulas the code cannot read (may be None)."""
    if not AVAILABLE:
        return []
    ctx = _Ctx(glossary)
    for name, meaning in types_from_prose(statement + "\n" + proof).items():
        if not type_from_words(ctx.meaning.get(name, "")):
            ctx.meaning[name] = meaning
    facts: list[Step] = []
    seen: set = set()

    def harvest(toks):
        try:
            seen.update(_Reader(list(toks), ctx).whole().free_symbols)
        except (Unreadable, TypeError, ValueError, AttributeError, ZeroDivisionError):
            pass

    for s in steps_of(statement):
        harvest(s["lhs"])
        if s["rel"] is None:
            continue
        harvest(s["rhs"])
        if s["rel"] in _SHOW and not s["claim"]:
            try:
                facts.append(_make(s, ctx))
            except (Unreadable, TypeError, ValueError, AttributeError, ZeroDivisionError):
                pass
    raw = steps_of(proof)
    parsed: list[list] = []
    for s in raw:
        if s["rel"] is None or s["rel"] not in _SHOW:
            parsed.append([s, None, "mention"])
            continue
        try:
            parsed.append([s, _make(s, ctx), ""])
        except (Unreadable, TypeError, ValueError, AttributeError, ZeroDivisionError) as e:
            parsed.append([s, None, str(e) or "not computation"])
    todo = [k for k, (s, st, why) in enumerate(parsed) if st is None and why != "mention" and _worth_translating(why)]
    if todo and translate:
        try:
            got = translate([{"id": f"s{k}", "tex": parsed[k][0]["tex"]} for k in todo[:14]], ctx) or {}
        except Exception:  # noqa: BLE001 - a failed translation leaves the steps to the proof check
            got = {}
        for k in todo[:14]:
            t = got.get(f"s{k}")
            if isinstance(t, Step):
                t.translated = True
                parsed[k][1:] = [t, ""]
            elif isinstance(t, str):
                parsed[k][2] = t
    out: list[dict] = []
    for s, st, why in parsed:
        if why == "mention":
            harvest(s["lhs"])
            if s["rel"] is not None:
                harvest(s["rhs"])
            continue
        rec = {"tex": s["tex"], "claim": s["claim"]}
        if st is None:
            rec.update(status="logical", detail=f"not computation ({why})")
        else:
            new = st.free - seen
            opaque, why2 = _opaque(st, ctx)
            if why2:
                rec.update(status="logical", detail=f"not computation ({why2})")
            elif not s["claim"] or (new and st.rel in ("=", "<=", ">=", "<", ">") and (len(new) < len(st.free) or any(
                    ctx.name_of(x).startswith("|") or "(" in ctx.name_of(x) for x in new))) or _fixes(st, facts, parsed, out):
                rec.update(status="given", detail="an assumption or a definition" if not s["claim"] else
                           f"introduces {', '.join(sorted(ctx.name_of(x).lstrip(chr(92)) for x in new))}" if new else
                           "fixes a value (a case or an index)")
            else:
                try:
                    known = facts + [x[1] for x, r in zip(parsed, _done(out, parsed)) if r]
                    constrained = set().union(*(h.free for h in known if not _assignment(h))) if known else set()
                    rec.update(check_step(st, ctx, known, pure=not (opaque or s.get("reason")) and st.free <= constrained))
                    if rec["status"] in ("counterexample", "unsupported") and st.rel == "=" and any(
                            a.is_Symbol and not b.free_symbols for a, b in ((st.lhs, st.rhs), (st.rhs, st.lhs))):
                        rec.update(status="given", detail="fixes a value (a case or an index)", values={})
                except Unreadable as e:
                    rec.update(status="logical", detail=f"not computation ({e})")
                except Exception as e:  # noqa: BLE001 - a step the checker cannot handle is left to the proof check
                    rec.update(status="unchecked", detail=f"the computation could not be done ({type(e).__name__})")
                if getattr(st, "translated", False):
                    rec["translated"] = True
                    rec["detail"] += "; read from the model's translation of the formula"
            seen.update(st.free)
        st_rec = rec
        out.append(st_rec)
        s["_rec"] = st_rec
    return out


def _fixes(st: Step, facts: list[Step], parsed: list, out: list[dict]) -> bool:
    """k = 2 with k free so far fixes a value (a case, an index), it does not claim one."""
    for a, b in ((st.lhs, st.rhs), (st.rhs, st.lhs)):
        if a.is_Symbol and not b.free_symbols:
            known = facts + [x[1] for x, r in zip(parsed, _done(out, parsed)) if r]
            return not any(a in h.free for h in known if h is not None and not (
                h.rel == "=" and {h.lhs.is_Symbol, h.rhs.is_Symbol} == {True, False} and not (h.lhs.free_symbols & h.rhs.free_symbols)
                and len(h.free) == 1))
    return False


def _done(out: list[dict], parsed: list) -> list[bool]:
    """For each parsed step before the current one: may it be used as a fact? (a false step may not)"""
    flags = []
    for s, st, why in parsed:
        rec = s.get("_rec")
        if rec is None:
            if why == "mention":
                flags.append(False)
                continue
            break
        flags.append(st is not None and rec.get("status") != "counterexample" and not rec.get("detail", "").startswith("fixes"))
    return flags


def _worth_translating(why: str) -> bool:
    return not re.search(r"not a number|a set|infinity|± is|function is", why)


def summary(results: list[dict]) -> dict:
    c = {k: 0 for k in ("proved", "exhaustive", "tested", "follows", "counterexample", "unsupported", "logical", "unchecked", "given")}
    for r in results:
        c[r["status"]] = c.get(r["status"], 0) + 1
    c["verified"] = c["proved"] + c["exhaustive"] + c["tested"] + c["follows"]
    return c


def for_model(results: list[dict]) -> str:
    """What the proof check is told."""
    if not results:
        return ""
    lines = []
    for r in results:
        if r["status"] in ("proved", "exhaustive", "tested", "follows"):
            lines.append(f"VERIFIED by computation ({r['detail']}): {r['tex']}")
        elif r["status"] == "counterexample":
            lines.append(f"FAILS by computation, {r['detail']}: {r['tex']}")
        elif r["status"] == "unsupported":
            lines.append(f"NOT IMPLIED by computation ({r['detail']}): {r['tex']}")
    if not lines:
        return ""
    return ("COMPUTER CHECKS (exact computation on the author's computer; the symbols' types are from the confirmed notation or, "
            "where none is given, taken as positive integers)\nDo not re-check the arithmetic of the steps marked VERIFIED; "
            "check only whether they are used correctly. A step marked FAILS is false for the values shown; decide whether an "
            "assumption of the proof excludes those values. A step marked NOT IMPLIED needs the reason the proof gives for it.\n" + "\n".join(lines))


# ----------------------------------------------------------------------------- the model as translator only

_PLAIN = re.compile(r"\s*(?:(\d+(?:\.\d+)?)|([A-Za-z_][A-Za-z0-9_]*)|(\*\*|[-+*/^(),]))")
_PLAIN_FUNCS = {"sqrt": lambda x: sp.sqrt(x), "root": lambda x, n: sp.root(x, n), "abs": lambda x: sp.Abs(x),
                "floor": lambda x: sp.floor(x), "ceil": lambda x: sp.ceiling(x), "factorial": lambda x: sp.factorial(x),
                "binomial": lambda a, b: sp.binomial(a, b), "gcd": lambda a, b: IGcd(a, b), "lcm": lambda a, b: ILcm(a, b),
                "totient": lambda x: sp.totient(x), "sigma": lambda x: sp.divisor_sigma(x), "tau": lambda x: sp.divisor_count(x),
                "log": lambda x, b=None: sp.log(x) if b is None else sp.log(x, b), "exp": lambda x: sp.exp(x),
                "sin": lambda x: sp.sin(x), "cos": lambda x: sp.cos(x), "max": lambda *a: sp.Max(*a), "min": lambda *a: sp.Min(*a)}


def read_plain(text: str, symbols: dict):
    """A small, safe reader for the model's plain syntax (never eval)."""
    toks, pos = [], 0
    text = text.strip()
    while pos < len(text):
        m = _PLAIN.match(text, pos)
        if not m or m.end() == pos:
            raise Unreadable(f"cannot read the translation near {text[pos:pos + 10]!r}")
        toks.append(m.group(1) or m.group(2) or ("^" if m.group(3) == "**" else m.group(3)))
        pos = m.end()
    i = 0

    def peek():
        return toks[i] if i < len(toks) else None

    def take():
        nonlocal i
        i += 1
        return toks[i - 1]

    def expr():
        e = -term() if peek() == "-" and take() else term()
        while peek() in ("+", "-"):
            e = e + term() if take() == "+" else e - term()
        return e

    def term():
        e = power()
        while peek() in ("*", "/"):
            e = e * power() if take() == "*" else e / power()
        return e

    def power():
        if peek() == "-":
            take()
            return -power()
        b = atom()
        if peek() == "^":
            take()
            return b ** power()
        return b

    def atom():
        t = take() if peek() is not None else None
        if t is None:
            raise Unreadable("the translation ends too early")
        if re.fullmatch(r"\d+(\.\d+)?", t):
            return sp.Rational(t)
        if t == "(":
            e = expr()
            if take() != ")":
                raise Unreadable("unbalanced translation")
            return e
        if re.fullmatch(r"[A-Za-z_]\w*", t):
            if peek() == "(":
                if t not in _PLAIN_FUNCS:
                    raise Unreadable(f"the translation uses {t}")
                take()
                a = [expr()]
                while peek() == ",":
                    take()
                    a.append(expr())
                if take() != ")":
                    raise Unreadable("unbalanced translation")
                return _PLAIN_FUNCS[t](*a)
            if t in ("pi", "E"):
                return sp.pi if t == "pi" else sp.E
            if t not in symbols:
                raise Unreadable(f"the translation uses an undeclared symbol {t}")
            return symbols[t]
        raise Unreadable(f"the translation has {t}")

    e = expr()
    if i != len(toks):
        raise Unreadable("the translation has extra text")
    return e


def from_translation(item: dict, ctx: _Ctx, original: str):
    """A model's translation of one step, checked against the formula's own numbers; returns a Step or a reason."""
    if item.get("not_computation") or item.get("not_computational"):
        return str(item.get("not_computation") or item.get("not_computational"))
    syms = {}
    for name, info in (item.get("symbols") or {}).items():
        if not re.fullmatch(r"[A-Za-z_]\w{0,30}", str(name)):
            return "the translation names a symbol oddly"
        words = info if isinstance(info, str) else json.dumps(info)
        typ = type_from_words(words) or "pos_int"
        if typ == "object":
            return "a symbol is not a number"
        label = str((info.get("means") if isinstance(info, dict) else "") or name)
        ctx.meaning.setdefault(label, words)
        syms[name] = ctx.sym(label) if label in ctx.symbols else _typed(ctx, label, typ)
    rel = {"=": "=", "==": "=", "<": "<", ">": ">", "<=": "<=", ">=": ">=", "!=": "!="}.get(str(item.get("rel", "")).strip())
    if not rel:
        return "the translation has no relation"
    lhs, rhs = read_plain(str(item.get("lhs", "")), syms), read_plain(str(item.get("rhs", "")), syms)
    mine = sorted(int(x) for x in re.findall(r"(?<![A-Za-z_\\{])\d+", original) if int(x) > 1)
    theirs = {int(x) for x in re.findall(r"\d+", f"{item.get('lhs')} {item.get('rhs')}")}
    if any(x not in theirs for x in mine):
        return "the model's translation dropped a number of the formula"
    return Step(lhs, rel, rhs, original)


def _typed(ctx: _Ctx, label: str, typ: str):
    kw = {"pos_int": dict(integer=True, positive=True), "nonneg_int": dict(integer=True, nonnegative=True), "int": dict(integer=True),
          "prime": dict(integer=True, positive=True, prime=True), "odd_prime": dict(integer=True, positive=True, prime=True, odd=True),
          "pos_real": dict(positive=True), "real": dict(real=True)}[typ]
    s = sp.Symbol("t" + str(len(ctx.symbols)) + "_" + re.sub(r"\W", "", label)[:12], **kw)
    ctx.symbols[label] = (s, typ)
    return s
