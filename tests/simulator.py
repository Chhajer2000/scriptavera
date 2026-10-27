# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Simulated models, literature and journal lookup for the automated tests.

Used only when the environment variable SCRIPTAVERA_MOCK=1 is set; nothing here is imported in normal operation, and
the app package itself contains no sample notes and no sample paper.

The simulated notes are six pages of elementary analysis (bounds for sums of reciprocal powers by comparison
with integrals), chosen because they exercise every path of the pipeline:

  page 1   a problem statement, a definition and a sketch            -> figure, definition
  pages 2-4  the same comparison argument carried out three times    -> "shorter arguments" finds a general lemma
  page 5   a proof whose last display contains a slip of the pen     -> the proof check finds it, a second opinion upholds it
  page 6   a consequence                                             -> corollary

Failure switches (environment):
  SCRIPTAVERA_MOCK_FAIL=<label prefix>          the first call whose label starts so fails with an unexpected error
  SCRIPTAVERA_MOCK_FAIL=limits:<label prefix>   ... fails as "every model has reached its limit", free again after SCRIPTAVERA_MOCK_RESET s
  SCRIPTAVERA_MOCK_FAIL=busy:<label prefix>     ... finds the strongest models resting for SCRIPTAVERA_MOCK_RESET s (a weaker one is free)
"""

from __future__ import annotations

import json
import os
import re
import time

from scriptavera.llm import ROLES, LLMError, Model, _Skip, router

DELAY = float(os.environ.get("SCRIPTAVERA_MOCK_DELAY", "0.05"))
TOP = ("sim-vision", "sim-text")

PAGES = [
    # 1
    r"""Problem: Find explicit bounds for the partial sums $H_n^{(s)} = \sum_{k=1}^{n} \frac{1}{k^s}$, where $s > 0$ is real and $n \ge 1$ is an integer.

[FIGURE: The graph of a decreasing function over the interval from $1$ to $n$, with rectangles of width $1$ under the curve whose upper right corners lie on the graph at the integers $2, \dots, n$.]

Idea: compare each term with the integral of $x^{-s}$ over an interval of length $1$.""",
    # 2
    r"""Claim: For all $n \ge 1$ we have $H_n^{(2)} \le 2 - \frac{1}{n}$.

Proof: Let $k \ge 2$. The function $x^{-2}$ is decreasing on $[k-1, k]$, so its smallest value on this interval is $k^{-2}$, and the interval has length $1$. Hence
$$\frac{1}{k^2} \le \int_{k-1}^{k} \frac{dx}{x^2}$$
Summing over $k = 2, \dots, n$ gives
$$\sum_{k=2}^{n} \frac{1}{k^2} \le \int_{1}^{n} \frac{dx}{x^2} = 1 - \frac{1}{n}$$
Adding the term $k = 1$, which is $1$, gives the claim.""",
    # 3
    r"""Claim: For all $n \ge 1$ we have $H_n^{(1)} \le 1 + \log n$.

Proof: Let $k \ge 2$. The function $x^{-1}$ is decreasing on $[k-1, k]$, so its smallest value on this interval is $k^{-1}$, and the interval has length $1$. Hence
$$\frac{1}{k} \le \int_{k-1}^{k} \frac{dx}{x}$$
Summing over $k = 2, \dots, n$ gives
$$\sum_{k=2}^{n} \frac{1}{k} \le \int_{1}^{n} \frac{dx}{x} = \log n$$
Adding the term $k = 1$, which is $1$, gives the claim.""",
    # 4
    r"""Theorem: Let $s > 1$. Then for all $n \ge 1$ we have $H_n^{(s)} \le \frac{s}{s-1}$. In particular $\zeta(s) \le \frac{s}{s-1}$.

Proof: Let $k \ge 2$. The function $x^{-s}$ is decreasing on $[k-1, k]$, so its smallest value on this interval is $k^{-s}$, and the interval has length $1$. Hence
$$\frac{1}{k^s} \le \int_{k-1}^{k} \frac{dx}{x^s}$$
Summing over $k = 2, \dots, n$ gives
$$\sum_{k=2}^{n} \frac{1}{k^s} \le \int_{1}^{n} \frac{dx}{x^s} = \frac{1 - n^{1-s}}{s-1} \le \frac{1}{s-1}$$
Adding the term $k = 1$ gives $H_n^{(s)} \le 1 + \frac{1}{s-1} = \frac{s}{s-1}$. Letting $n \to \infty$ gives the bound for $\zeta(s)$.""",
    # 5  (the last display should end with \log(n+1): a slip of the pen, left in on purpose)
    r"""Claim: For all $n \ge 1$ we have $H_n^{(1)} \ge \log(n+1)$.

Proof: Let $k \ge 1$. The function $x^{-1}$ is decreasing on $[k, k+1]$, so its largest value on this interval is $k^{-1}$. Hence
$$\frac{1}{k} \ge \int_{k}^{k+1} \frac{dx}{x}$$
Summing over $k = 1, \dots, n$ gives
$$\sum_{k=1}^{n} \frac{1}{k} \ge \int_{1}^{n+1} \frac{dx}{x} = \log n$$
which is the claim.""",
    # 6
    r"""Consequence: the two claims for $s = 1$ give
$$\log(n+1) \le H_n^{(1)} \le 1 + \log n$$
so the harmonic sums grow like $\log n$ and the tail of the series is not small. For $s > 1$ the sums are bounded, by the theorem.""",
]

# How the simulated writer arranges each page: (environment, id of the result or definition).
ROLE_OF_PAGE = {1: ("definition", "D1"), 2: ("lemma", "R1"), 3: ("lemma", "R2"), 4: ("theorem", "R3"),
                5: ("proposition", "R4"), 6: ("corollary", "R5")}
ALTERED = (r"\frac{1 - n^{1-s}}{s-1}", r"\frac{1 - n^{1-s}}{s+1}")       # one formula the simulated writer gets wrong
SLIP = (r"\int_{1}^{n+1} \frac{dx}{x} = \log n", r"\int_{1}^{n+1} \frac{dx}{x} = \log(n+1)")


def models() -> list[Model]:
    """Two strong models (one reads images, one does not) and a weaker one that can do everything."""
    out = []
    for mid, vision, level in (("sim-vision", True, 90), ("sim-text", False, 90), ("sim-small", True, 60)):
        m = Model(provider="mock", id=mid, vision=vision, req_tokens=200_000, out_cap=16000, interval=0.0)
        m.scores = {}
        for r in ROLES:
            if r.startswith("vision"):
                m.scores[r] = level if vision else 0
            else:
                m.scores[r] = level if mid != "sim-vision" else 40
        out.append(m)
    return out


def papers() -> list[dict]:
    from scriptavera.literature import _paper
    base = [
        ("Simulated paper A: explicit bounds for partial sums of the harmonic series", "Example", "Alice", 2015,
         "Simulated Journal of Analysis", "12", "275--286"),
        ("Simulated paper B: integral comparison for monotone functions", "Sample", "Boris", 2018,
         "Simulated Bulletin of Mathematics", "49", "515--530"),
        ("Simulated paper C: partial sums of the zeta series", "Placeholder", "Chen", 2019,
         "Simulated Communications in Analysis", "47", "1043--1056"),
        ("Simulated paper D: inequalities for sums of reciprocal powers", "Demo", "Dana", 2011,
         "Simulated Archive of Mathematics", "96", "201--210"),
        ("Simulated paper E: a survey of elementary inequalities for series", "Mock", "Eli", 2020, "", "", ""),
        ("Simulated paper F: colourings of random graphs", "Offtopic", "Fay", 2017,
         "Simulated Journal of Combinatorics", "3", "1--20"),
    ]
    out = []
    for i, (title, fam, giv, year, venue, vol, pages) in enumerate(base, 1):
        out.append(_paper(
            title=title, authors=[{"given": giv, "family": fam}], year=year, venue=venue, volume=vol, pages=pages,
            doi=f"10.0000/simulated.{i}" if venue else "", arxiv="" if venue else "0000.00000",
            url=f"https://example.org/simulated/{i}",
            abstract=("Simulated abstract. We give explicit upper and lower bounds for partial sums of series of "
                      "reciprocal powers by comparing the terms with integrals of a decreasing function."
                      if i < 6 else "Simulated abstract. We estimate the chromatic number of random regular graphs."),
            cats=["math.CA"] if i < 6 else ["math.CO"], msc=["26D15"] if i < 6 else ["05C80"],
            cited_by=40 - 5 * i, sources=["Simulated library"]))
    return out


def journals(query: str) -> list[dict]:
    from scriptavera.journals import _journal
    pool = [
        _journal("Simulated Journal of Analysis", "Springer Nature (simulated)", "0000-0001", "https://example.org/sja",
                 works=4200, h_index=61, country="DE", source="Simulated"),
        _journal("Simulated Annals of Inequalities", "Elsevier (simulated)", "0000-0002", "https://example.org/sai",
                 works=1800, h_index=44, country="NL", source="Simulated"),
        _journal("Simulated Proceedings in Mathematics", "American Mathematical Society (simulated)", "0000-0003",
                 "https://example.org/spm", works=9100, h_index=88, country="US", source="Simulated"),
        _journal("Simulated Quarterly of Pure Mathematics", "Independent Press (simulated)", "0000-0004", "",
                 works=600, h_index=17, country="GB", source="Simulated"),
    ]
    words = [w for w in re.findall(r"[a-z]+", query.lower()) if len(w) > 2]
    hits = [j for j in pool if any(w in j["name"].lower() for w in words)]
    return hits or ([] if "nothing" in query.lower() else pool[:2])


# ------------------------------------------------------------------ helpers

def _page_no(label: str) -> int:
    m = re.search(r"page (\d+)", label)
    return int(m.group(1)) if m else 1


# With SCRIPTAVERA_MOCK_DRAWINGS=1 the proof on page 3 has a drawing of its own: the author's picture of the comparison
# step, which only explains the argument (the judge of drawings finds it inside the proof).
PROOF_DRAWING = (r"[FIGURE: The graph of $1/x$ over the interval $[k-1, k]$, with the rectangle of height $1/k$ lying under "
                 r"the curve.]")
AFTER = "\\int_{k-1}^{k} \\frac{dx}{x}$$\n"


def _page_text(n: int) -> str:
    t = PAGES[(n - 1) % len(PAGES)]
    if os.environ.get("SCRIPTAVERA_MOCK_DRAWINGS") and (n - 1) % len(PAGES) == 2:
        t = t.replace(AFTER, AFTER + PROOF_DRAWING + "\n", 1)
    return t


SEEN: dict[str, str] = {}           # the last prompt sent with each label, for the tests to inspect


def _displays(text: str) -> str:
    return re.sub(r"\$\$(.+?)\$\$", lambda m: "\\[ " + m.group(1).strip() + " \\]", text, flags=re.S)


def typeset(n: int, figures: list[str], text: str | None = None) -> str:
    """What the simulated writer makes of page n: the author's formulas (from the transcript it is given), the
    paper's arrangement."""
    env, rid = ROLE_OF_PAGE[(n - 1) % len(PAGES) + 1]
    t = _displays(text if text is not None else _page_text(n))
    if env == "definition":
        fig = "".join(f"\n\\insertfigure{{{f}}}\n" for f in figures)
        ref = f"; see Figure~\\ref{{fig:{figures[0]}}}" if figures else ""
        return ("We study the following problem: find explicit bounds for the partial sums defined below.\n\n"
                f"\\begin{{definition}}\\label{{def:{rid}}}\n"
                "For a real number $s > 0$ and an integer $n \\ge 1$ we put $H_n^{(s)} = \\sum_{k=1}^{n} \\frac{1}{k^s}$.\n"
                "\\end{definition}\n" + fig +
                f"\nThe idea is to compare each term with the integral of $x^{{-s}}$ over an interval of length $1${ref}")
    m = re.match(r"(?:Claim|Theorem): (.+?)\n\nProof: (.+)\Z", t, flags=re.S)
    if m:
        return (f"\\begin{{{env}}}\\label{{res:{rid}}}\n{m.group(1).strip()}\n\\end{{{env}}}\n"
                f"\\begin{{proof}}\n{m.group(2).strip()}\n\\end{{proof}}")
    body = t.split("give", 1)[1].strip()
    display, rest = body.split("\\]", 1)
    return ("The two bounds for $s = 1$ combine as follows.\n\n"
            f"\\begin{{{env}}}\\label{{res:{rid}}}\nFor all $n \\ge 1$ we have\n{display.strip()} \\]\n\\end{{{env}}}\n"
            "\\begin{proof}\nCombine Lemma~\\ref{res:R2} and Proposition~\\ref{res:R4}.\n\\end{proof}\n\n"
            "Thus " + rest.strip().removeprefix("so ").strip())


def _passage(user: str, *heads: str) -> str:
    for h in heads:
        if h in user:
            return user.split(h, 1)[1].strip()
    return user.strip()


def _polish(text: str) -> str:
    """A copy-edit that touches wording only."""
    t = text.replace("gives the claim", "yields the claim").replace(" Hence\n", " Hence,\n")
    t = t.replace("Summing over", "Summation over").replace("which is the claim", "as claimed")
    t = t.replace("among the first series met in analysis", "among the first series encountered in analysis")
    return t.replace("The idea is to compare", "The method is to compare")


LONG_ABSTRACT = (
    "We give explicit bounds for the partial sums $H_n^{(s)} = \\sum_{k=1}^{n} k^{-s}$ of the series of reciprocal "
    "powers, where $s > 0$ is real and $n \\ge 1$ is an integer. The method is elementary throughout: each term is "
    "compared with the integral of the decreasing function $x^{-s}$ over an interval of length one, and the "
    "resulting inequalities are summed. For $s = 2$ this gives $H_n^{(2)} \\le 2 - 1/n$, and for $s = 1$ it gives "
    "the two-sided estimate $\\log(n+1) \\le H_n^{(1)} \\le 1 + \\log n$, so that the harmonic sums grow like "
    "$\\log n$. Our main result treats every exponent $s > 1$ at once: for all $n \\ge 1$ we have "
    "$H_n^{(s)} \\le s/(s-1)$, and consequently $\\zeta(s) \\le s/(s-1)$ for the Riemann zeta function. All "
    "constants are explicit, no asymptotic notation is used, and every bound holds for every $n$ rather than for "
    "large $n$ only. The arguments use nothing beyond the monotonicity of the integrand and are organised so "
    "that the comparison step is stated once and then quoted wherever it is needed in the sequel. We also "
    "explain how the same comparison yields lower bounds when the interval of integration is shifted by one.")


# ------------------------------------------------------------------ answers

def respond(label: str, system: str, user: str, vision: bool, model: Model | None = None) -> str:
    fail = os.environ.get("SCRIPTAVERA_MOCK_FAIL", "")
    if fail.startswith("busy:") and label.startswith(fail[5:]) and model is not None and model.id in TOP:
        until = time.time() + float(os.environ.get("SCRIPTAVERA_MOCK_RESET", "4"))
        for m in router.models:
            if m.id in TOP:
                m.cooldown_until, m.note = until, "daily limit reached"
        os.environ["SCRIPTAVERA_MOCK_FAIL"] = ""
        raise _Skip("daily limit reached")
    if fail.startswith("limits:") and label.startswith(fail[7:]):
        os.environ["SCRIPTAVERA_MOCK_FAIL"] = ""
        raise LLMError("Every model that can do this job has reached its free-tier limit (simulated).",
                       kind="limits", retry_at=time.time() + float(os.environ.get("SCRIPTAVERA_MOCK_RESET", "4")))
    if fail and ":" not in fail and label.startswith(fail):
        os.environ["SCRIPTAVERA_MOCK_FAIL"] = ""          # fail once, so that Continue succeeds
        raise RuntimeError("simulated failure")
    n = _page_no(label)
    SEEN[label] = user

    # ---- drawings: found on the page, their labels read, the redrawing checked and corrected
    if label.startswith("Find the drawings"):
        items = re.findall(r"(?m)^(\d+)\. ", user)
        out = []
        for k in items:
            if k == "1":
                out.append({"n": 1, "found": True, "box": [255, 120, 520, 880], "same_as": None})
            else:
                out.append({"n": int(k), "found": False, "box": None, "same_as": None})
        return json.dumps({"drawings": out})
    if label.startswith("Read the labels of figure"):
        boxes = [int(b) for b in re.findall(r"(?m)^box (\d+):", user)]
        return json.dumps({"labels": [], "drawing": [], "ignore": boxes, "missed": [],
                           "caption": "Rectangles of width $1$ under the graph of a decreasing function."})
    if label.startswith("Check figure"):
        return json.dumps({"same": True, "problems": []})
    if label.startswith("Correct figure"):
        m = re.search(r"\\begin\{tikzpicture\}.*\\end\{tikzpicture\}", user, re.S)
        return "<tikz>\n" + (m.group(0) if m else "") + "\n</tikz>"

    # ---- reading the notes
    if label.startswith("Locate"):
        if "tail" in label:
            return json.dumps({"found": False, "boxes": [], "note": "The word written here looks like 'total', not 'tail'."})
        return json.dumps({"found": True, "boxes": [[130, 620, 190, 760], [455, 300, 520, 430]], "note": ""})
    if label.startswith("Scout"):
        prose = re.sub(r"\$\$.*?\$\$|\$[^$]+\$", "⟦math⟧", _page_text(n), flags=re.S)
        return (f"<kind>content</kind>\n<prose>\n{prose}\n</prose>\n<printed>\nnone\n</printed>\n"
                "<terms>\npartial sum, decreasing function, integral, harmonic sum, series\n</terms>\n<names>\nnone\n</names>")
    if label.startswith("Identify topic"):
        return json.dumps({
            "subfield": "Elementary inequalities for series",
            "problem": "The author appears to look for explicit bounds for the partial sums of the series of reciprocal "
                       "powers, obtained by comparing each term with an integral of a decreasing function.",
            "keywords": ["partial sums", "reciprocal powers", "harmonic sums", "integral comparison", "explicit bounds",
                         "decreasing function", "zeta series", "elementary inequalities"],
            "queries": ["explicit bounds partial sums reciprocal powers", "harmonic sum integral comparison bound",
                        "partial sums zeta series explicit inequality", "sum integral comparison decreasing function",
                        "bounds for harmonic numbers", "inequalities for series"],
            "anchor": "", "confidence": 0.85, "missing": "Whether the logarithm is natural."})
    if label.startswith("Refine the topic with your comment"):
        assert "THE AUTHOR'S COMMENT" in user and "The problem, in the author's words" in user
        return json.dumps({
            "subfield": "Explicit inequalities for partial sums of series",
            "problem": "Explicit upper and lower bounds for the partial sums of the series of reciprocal powers, for every "
                       "exponent, by comparison with integrals.",
            "keywords": ["partial sums", "reciprocal powers", "integral test", "explicit bounds", "harmonic numbers"],
            "queries": ["explicit bounds partial sums reciprocal powers", "integral test explicit error harmonic numbers",
                        "partial sums zeta series explicit inequality"],
            "anchor": "", "confidence": 0.92, "missing": ""})
    if label.startswith("Rank"):
        idx = [int(x) for x in re.findall(r"^\[(\d+)\]", user, flags=re.M)]
        scores = []
        for i in idx:
            off = f"[{i}] Simulated paper F" in user
            scores.append({"i": i, "score": 1 if off else max(5, 10 - i), "why": "different field" if off else "same objects"})
        return json.dumps({"scores": scores})
    if label.startswith("Survey"):
        t = _page_text(n)
        if n == 6:
            t = t.replace(r"\le H_n^{(1)} \le", r"\le H_m^{(1)} \le")       # a misreading of the first pass
        squashed = re.sub(r"[\s${}]+", "", t)
        rows = []
        for sym, use, state, definition in (
                (r"H_n^{(s)}", "the partial sum being bounded", "DEFINED", r"$H_n^{(s)} = \sum_{k=1}^{n} \frac{1}{k^s}$"),
                (r"\zeta(s)", "appears in the bound after the theorem", "UNDEFINED", ""),
                (r"\log", "bounds the sums for s = 1", "UNDEFINED", ""),
                (r"H_m^{(1)}", "appears once in a chain of inequalities", "UNDEFINED", "")):
            if re.sub(r"[\s${}]+", "", sym) in squashed:
                rows.append(f"{sym} || {use} || {state} || {definition}")
        terms = "tail || 'the tail of the series is not small'" if "tail" in t else ""
        return (f"<kind>content</kind>\n<transcript>\n{t}\n</transcript>\n<symbols>\n" + "\n".join(rows)
                + f"\n</symbols>\n<terms>\n{terms}\n</terms>\n<gist>Works towards a bound for the partial sums.</gist>")
    if label.startswith("Resolve notation"):
        return r"""<entry>
symbol: \zeta(s)
kind: symbol
status: undefined
meaning: the Riemann zeta function, that is, the limit of $H_n^{(s)}$ as $n \to \infty$, for $s > 1$
confidence: 0.9
basis: paper Placeholder2019Simulated
alternatives: the Hurwitz zeta function
question: Is $\zeta(s)$ the Riemann zeta function, the limit of your sums $H_n^{(s)}$?
</entry>
<entry>
symbol: \log
kind: symbol
status: ambiguous
meaning: the natural logarithm
confidence: 0.7
basis: notes p.3
alternatives: the logarithm to base 10 ; the logarithm to base 2
question: Is log the natural logarithm throughout?
</entry>
<entry>
symbol: tail
kind: term
status: undefined
meaning: the remainder $\sum_{k > n} k^{-s}$ of the series after the first $n$ terms
confidence: 0.6
basis: notes p.6
alternatives: the partial sums themselves
question: By "the tail of the series" do you mean the remainder after $n$ terms?
</entry>
<entry>
symbol: H_n^{(s)}
kind: symbol
status: defined
meaning: the partial sum $H_n^{(s)} = \sum_{k=1}^{n} \frac{1}{k^s}$
confidence: 0.98
basis: notes p.1
alternatives: none
question:
</entry>
<entry>
symbol: H_m^{(1)}
kind: symbol
status: misread
meaning: probably $H_n^{(1)}$, which is used everywhere else
confidence: 0.5
basis: notes p.6
alternatives: none
question:
</entry>
<questions>
page 5 || Does the last display of the proof end with $\log n$ or with $\log(n+1)$?
</questions>"""
    if label.startswith("Refine search"):
        return json.dumps({"queries": ["explicit bounds partial sums reciprocal powers", "harmonic numbers logarithm bounds",
                                       "integral comparison decreasing function sum"],
                           "keywords": ["partial sums", "harmonic numbers", "integral comparison"]})
    if label.startswith("Read the journal"):
        return json.dumps({"journal": "Simulated Journal of Analysis", "abstract_min_words": None, "abstract_max_words": 200,
                           "keywords_min": 3, "keywords_max": 5, "msc": "required", "ref_style": "numeric",
                           "ref_order": "alphabetical", "anonymous": False, "line_numbers": True, "double_spacing": None,
                           "declarations": True, "latex_class": "amsart", "page_limit": 30,
                           "notes": ["Figures must be supplied as vector graphics.", "Submit a single PDF file."]})
    if label.startswith("Read"):
        return """<verdict>related</verdict>
<summary>Simulated notes. The paper gives explicit bounds for partial sums of series of reciprocal powers.</summary>
<results>
- Theorem 1: a simulated two-sided bound for the harmonic sums.
</results>
<notation>
- H_n: the n-th harmonic sum
</notation>
<relation>It poses the problem of finding explicit bounds for the partial sums and treats the case s = 1.</relation>
<citable>
- Explicit two-sided bounds for the harmonic sums are known.
- The paper poses the problem of bounding the partial sums of the series of reciprocal powers explicitly.
</citable>"""
    if label.startswith("Write context brief"):
        return ("PROBLEM\nExplicit bounds for the partial sums of the series of reciprocal powers, a problem posed in "
                "[Example2015Simulated].\nKNOWN RESULTS\nTwo-sided bounds for the harmonic sums [Example2015Simulated].\n"
                "NOTATION\nH_n^(s) = the n-th partial sum of the series of k^(-s)\nlog = natural logarithm\n"
                "THE AUTHOR'S NOTES\nUpper bounds for s = 2, s = 1 and all s > 1, and a lower bound for s = 1.\n"
                "READING HAZARDS\nn and n+1 inside a logarithm look alike; the signs for 'at most' and 'at least' look alike.")
    if label.startswith("Transcribe"):
        unc = {2: "\\frac{1}{n} || the denominator could be n or n+1 || \\frac{1}{n+1}",
               4: "s > 1 || the relation could be 'greater' or 'greater or equal' || s \\ge 1",
               5: "= \\log n || the argument of the logarithm is hard to read: n or n+1 || = \\log(n+1)",
               6: "the sums are bounded || singular or plural is unclear || the sum is bounded"}.get((n - 1) % len(PAGES) + 1, "none")
        # answers as weaker models gave them (SCRIPTAVERA_MOCK_BROKEN="2,3"): page 2 without its opening tag and with
        # web markup, page 3 repeating one command endlessly (its second reading is sound, unless "3!" is given)
        broken = os.environ.get("SCRIPTAVERA_MOCK_BROKEN", "").split(",")
        if n == 2 and "2" in broken:
            return (f"<u>Claim</u>" + _page_text(n)[5:] + "\n</transcript>\n<uncertain>\nnone\n</uncertain>\n"
                    "<changes>\nAdded underlining to the word Claim.\n</changes>")
        if n == 3 and ("3!" in broken or ("3" in broken and "(again)" not in label)):
            return "<transcript>\n" + _page_text(n)[:60] + " " + "\\quad " * 300 + "\n</transcript>\n<uncertain>\nnone\n</uncertain>"
        return f"<transcript>\n{_page_text(n)}\n</transcript>\n<uncertain>\n{unc}\n</uncertain>\n<changes>\nnone\n</changes>"
    if label.startswith("Check transcript"):
        return ("<flags>\npage 5 || = \\log n || the right-hand side does not match the claim being proved || = \\log(n+1)\n"
                "page 2 || Adding the term || possible spelling slip in the first word || Adding the terms\n"
                # the shape models often use instead: a description where the quotation belongs, no page, and the
                # suggestion described in the explanation column
                "page 0 || The argument of the logarithm is written n here but n+1 everywhere else in the notes, which suggests a misreading. "
                "|| suggested reading: replace \u201c\\log n\u201d by \u201c\\log(n+1)\u201d (so the line reads $\\int_{1}^{n+1} \\frac{dx}{x} = \\log(n+1)$). ||\n</flags>")
    if label.startswith("Decide which drawings are for the paper"):
        # as a careful reader would: a drawing inside a proof that pictures a step the text states is an explanation;
        # the picture of the method next to the problem is for the paper. SCRIPTAVERA_MOCK_DRAWINGS=relies makes the
        # reader say the proof takes something from the drawing, so that the code asks the author instead.
        out = []
        for fid, where in re.findall(r"DRAWING (f\d+): .*?\nWhere it sits: ([^\n]*)", user, flags=re.S):
            if where.startswith("inside a proof"):
                relies = os.environ.get("SCRIPTAVERA_MOCK_DRAWINGS") == "relies"
                out.append({"id": fid, "use": "explanation", "sure": True,
                            "why": "It sits in the proof and pictures the comparison that the text states in formulas.",
                            "illustrates": "the step of the proof where a term is compared with an integral over an interval of length $1$",
                            "needed": "the rectangle that the inequality compares" if relies else "none"})
            else:
                out.append({"id": fid, "use": "paper", "sure": True, "illustrates": "", "needed": "none",
                            "why": "It shows the comparison of the sum with an integral, the method the notes announce with the problem."})
        return json.dumps({"drawings": out})

    # ---- the first draft
    if label.startswith("Understand"):
        return r"""<problem>Find explicit bounds for the partial sums $H_n^{(s)} = \sum_{k=1}^{n} \frac{1}{k^s}$, where $s > 0$ is real and $n \ge 1$ is an integer.</problem>
<source>Example2015Simulated</source>
<setting>Throughout, $n \ge 1$ is an integer and $s > 0$ is real. $\log$ is the natural logarithm and $\zeta(s)$ is the Riemann zeta function.</setting>
<result>
id: R1
kind: lemma
statement: For all $n \ge 1$ we have $H_n^{(2)} \le 2 - \frac{1}{n}$.
pages: 2
depends: D1
role: auxiliary
</result>
<result>
id: R2
kind: lemma
statement: For all $n \ge 1$ we have $H_n^{(1)} \le 1 + \log n$.
pages: 3
depends: D1
role: auxiliary
</result>
<result>
id: R3
kind: theorem
statement: Let $s > 1$. Then for all $n \ge 1$ we have $H_n^{(s)} \le \frac{s}{s-1}$. In particular $\zeta(s) \le \frac{s}{s-1}$.
pages: 4
depends: D1
role: main
</result>
<result>
id: R4
kind: proposition
statement: For all $n \ge 1$ we have $H_n^{(1)} \ge \log(n+1)$.
pages: 5
depends: D1
role: intermediate
</result>
<result>
id: R5
kind: corollary
statement: For all $n \ge 1$ we have $\log(n+1) \le H_n^{(1)} \le 1 + \log n$.
pages: 6
depends: R2, R4
role: intermediate
</result>
<result>
id: R6
kind: lemma
statement: For $k \ge 2$ we have $\frac{1}{k^s} \le \int_{k-1}^{k} \frac{dx}{x^s}$.
author_words: Hence $\frac{1}{k^s} \le \int_{k-1}^{k} \frac{dx}{x^s}$
pages: 4
depends: none
role: auxiliary
</result>
<definition>
id: D1
name: $H_n^{(s)}$
text: $H_n^{(s)} = \sum_{k=1}^{n} \frac{1}{k^s}$ for real $s > 0$ and integers $n \ge 1$
page: 1
</definition>
<storyline>The notes fix the partial sums D1 and compare each term with an integral over an interval of length one. This gives R1 for the exponent two and R2 for the exponent one. The same comparison for a general exponent greater than one gives the main result R3. Reversing the comparison gives the lower bound R4. Together R2 and R4 give the two-sided estimate R5.</storyline>
<contribution>Two-sided bounds for the harmonic sums are known [Example2015Simulated]. The notes give one elementary argument that covers every exponent greater than one, with explicit constants.</contribution>
<notation_choice>none</notation_choice>
<concerns>CONCERNS</concerns>""".replace("CONCERNS", "none" if os.environ.get("SCRIPTAVERA_MOCK_CLEAN") == "1" else
                                                r"On page 5 the last display ends with $\log n$, while the claim being proved is a bound by $\log(n+1)$.")
    if label.startswith("Check the results against the notes"):
        # the first reading lifted a step of the proof of R3 out as a lemma of its own: the check puts it back
        return ("R1 || result || none\nR2 || result || none\nR3 || result || none\nR4 || result || none\n"
                "R5 || result || none\nR6 || part of R3 || it is the comparison step inside the proof of R3")
    if label.startswith("Assess the notes"):
        if os.environ.get("SCRIPTAVERA_MOCK_CLEAN") == "1":
            blocks = "".join(f"<result>\nid: R{i}\nproof: complete\nwhere: none\nknown: new\nevidence: none\n</result>\n" for i in range(1, 6))
            return blocks + ("<main>established</main>\n<main_note>The main theorem is proved in full.</main_note>\n"
                             "<unfinished>none</unfinished>\n<recommend>article</recommend>\n<recommend_note>Every result is proved.</recommend_note>")
        r2 = "sketch" if os.environ.get("SCRIPTAVERA_MOCK_SKETCH") == "1" else "complete"
        return ("<result>\nid: R1\nproof: complete\nwhere: none\nknown: new\nevidence: none\n</result>\n"
                f"<result>\nid: R2\nproof: {r2}\nwhere: none\nknown: known Example2015Simulated\n"
                "evidence: The upper bound for the harmonic sums is stated there.\n</result>\n"
                "<result>\nid: R3\nproof: complete\nwhere: none\nknown: new\nevidence: none\n</result>\n"
                "<result>\nid: R4\nproof: gap\nwhere: On page 5 the last display gives $\\log n$, not the bound $\\log(n+1)$ that is claimed.\n"
                "known: new\nevidence: none\n</result>\n"
                "<result>\nid: R5\nproof: complete\nwhere: none\nknown: known NotARealKey2001\nevidence: invented\n</result>\n"
                "<main>established</main>\n<main_note>The main theorem is proved; the lower bound for the harmonic sums has a gap.</main_note>\n"
                "<unfinished>none</unfinished>\n<recommend>article</recommend>\n"
                "<recommend_note>The main result is proved and is not in the papers read; one auxiliary bound needs its last step.</recommend_note>")
    if label.startswith("Rewrite the introduction for the chosen citations"):
        intro = user.split("INTRODUCTION\n", 1)[1].split("\n\nPAPERS TO CITE", 1)[0]
        chosen = re.findall(r"(?m)^\[([A-Za-z0-9]+)\]", user.split("PAPERS TO CITE", 1)[1])
        intro = re.sub(r"\\cite\{([^{}]+)\}", lambda m: ("\\cite{" + ",".join(k for k in m.group(1).split(",") if k.strip() in chosen) + "}")
                       if any(k.strip() in chosen for k in m.group(1).split(",")) else "", intro)
        extra = [k for k in chosen if k not in intro]
        return intro + "".join(f" A related treatment is given in \\cite{{{k}}}." for k in extra)
    if label.startswith("Propose more titles"):
        return "<titles>\nComparison of sums and integrals for reciprocal powers\nA short note on harmonic sums\n</titles>"
    if label.startswith("Write introduction"):
        return ("Sums of reciprocal powers are among the first series met in analysis, and explicit bounds for their "
                "partial sums are used throughout the subject. For a real number $s > 0$ and an integer $n \\ge 1$ we write "
                "$H_n^{(s)} = \\sum_{k=1}^{n} \\frac{1}{k^s}$.\n\n"
                "Explicit two-sided bounds for the harmonic sums are known \\cite{Example2015Simulated}, and the comparison "
                "of a sum with an integral is treated in \\cite{Sample2018Simulated}. A statement for which no source was "
                "found is attributed to \\cite{Nobody1999Invented}.\n\n"
                "The problem of bounding $H_n^{(s)}$ explicitly for every exponent is posed in \\cite{Example2015Simulated}. "
                "In this paper we give an elementary answer. Our main result states that for every $s > 1$ and all $n \\ge 1$ "
                "we have\n\\[ H_n^{(s)} \\le \\frac{s}{s-1}, \\]\nand in particular $\\zeta(s) \\le \\frac{s}{s-1}$ for the "
                "Riemann zeta function. For $s = 1$ we obtain $\\log(n+1) \\le H_n^{(1)} \\le 1 + \\log n$, where $\\log$ is the "
                "natural logarithm. All constants are explicit and the bounds hold for every $n$. The arguments rest on a "
                "single comparison of a term of the series with an integral of a decreasing function over an interval of "
                "length one, which is carried out once and then applied to each exponent in turn. We also record the case "
                "$s = 2$ separately, since it is the one most often quoted.")
    if label.startswith("Decide paper structure"):
        return """<section>
title: Preliminaries
items: D1
from_page: 1
purpose: Fixes the partial sums and the idea of the comparison.
</section>
<section>
title: Upper bounds
items: R1, R2, R3
from_page: 2
purpose: Proves the upper bounds for the exponents two and one, and the main theorem.
</section>
<section>
title: The harmonic sums
items: R4, R5
from_page: 5
purpose: Proves the lower bound for the exponent one and combines the two bounds.
</section>
<organisation>The paper is organised as follows. Section~2 fixes the notation. Section~3 contains the upper bounds and the main theorem. Section~4 treats the harmonic sums.</organisation>"""
    if label.startswith("Write section"):
        title = re.search(r'WRITE THIS SECTION\nSection \d+, "([^"]+)"', user)
        figs = re.findall(r"(?m)^- (f\d+):", user.split("FIGURES ON THESE PAGES", 1)[-1].split("WRITE THIS SECTION", 1)[0])
        notes = user.split("NOTES FOR THIS SECTION", 1)[1]
        out = [] if "already written" in user.split("WRITE THIS SECTION", 1)[1].split("NOTES FOR THIS SECTION", 1)[0] \
            else [f"\\section{{{title.group(1) if title else 'Results'}}}"]
        parts = re.split(r"(?m)^%%% page (\d+)\s*$", notes)
        for i in range(1, len(parts), 2):
            pn, text = int(parts[i]), parts[i + 1].strip()
            mine = [f for f in figs if f"[FIGURE {f}:" in text]
            out.append(f"%%% page {pn}\n" + typeset(pn, mine, text))
        return "\n\n".join(out).replace(*ALTERED)
    if label.startswith("Restore formulas"):
        return user.split("FRAGMENT TO CORRECT\n", 1)[1].replace(ALTERED[1], ALTERED[0])

    # ---- revision
    if os.environ.get("SCRIPTAVERA_MOCK_CLEAN") == "1":
        if label.startswith("Find shared arguments"):
            return "<none/>"
        if label.startswith("Check proof of"):
            return "<verdict>correct</verdict>\n<summary>Every step follows.</summary>"
        if label.startswith("Referee report"):
            return ("<summary>A short, correct note.</summary>\n<recommendation>accept</recommendation>\n<point>\ntype: typography\n"
                    "where: whole paper\ncomment: Some displays lack punctuation.\naction: Punctuate them.\n</point>")
    if label.startswith("Find shared arguments"):
        return """<proposal>
kind: general-lemma
affects: res:R1, res:R2, res:R3
idea: The proofs of res:R1, res:R2 and res:R3 carry out the same comparison of a term with an integral, for three different exponents. State the comparison once, for a positive decreasing function, as a lemma placed before res:R1, and quote it in the three proofs.
benefit: one argument instead of three
</proposal>
<proposal>
kind: same-argument
affects: res:R4
idea: The proof of res:R4 looks like the proof of res:R2 and could be replaced by a reference to it.
benefit: a shorter proof
</proposal>"""
    if label.startswith("Rewrite for S1"):
        return r"""<new_result>
before: res:R1
kind: lemma
statement: Let $f$ be a positive decreasing function on $[1, \infty)$. Then for every integer $n \ge 1$ we have
\[ \sum_{k=2}^{n} f(k) \le \int_{1}^{n} f(x)\,dx. \]
proof: Let $k \ge 2$. Since $f$ is decreasing on $[k-1, k]$, we have $f(k) \le \int_{k-1}^{k} f(x)\,dx$. Summing over $k = 2, \dots, n$ gives the claim.
</new_result>
<proof for="res:R1">
By Lemma~\ref{res:NEW} with $f(x) = x^{-2}$,
\[ \sum_{k=2}^{n} \frac{1}{k^2} \le \int_{1}^{n} \frac{dx}{x^2} = 1 - \frac{1}{n}. \]
Adding the term $k = 1$, which is $1$, gives the claim.
</proof>
<proof for="res:R2">
By Lemma~\ref{res:NEW} with $f(x) = x^{-1}$,
\[ \sum_{k=2}^{n} \frac{1}{k} \le \int_{1}^{n} \frac{dx}{x} = \log n. \]
Adding the term $k = 1$, which is $1$, gives the claim.
</proof>
<proof for="res:R3">
By Lemma~\ref{res:NEW} with $f(x) = x^{-s}$,
\[ \sum_{k=2}^{n} \frac{1}{k^s} \le \int_{1}^{n} \frac{dx}{x^s} = \frac{1 - n^{1-s}}{s-1} \le \frac{1}{s-1}. \]
Adding the term $k = 1$ gives $H_n^{(s)} \le 1 + \frac{1}{s-1} = \frac{s}{s-1}$. Letting $n \to \infty$ gives the bound for $\zeta(s)$.
</proof>"""
    if label.startswith("Rewrite for"):
        return ('<proof for="res:R4">\nBy the same argument as in the proof of Lemma~\\ref{res:R2}, with the interval '
                "$[k, k+1]$ in place of $[k-1, k]$, we obtain the claim.\n</proof>")
    if label.startswith("Check S1"):
        return ("<verdict>valid</verdict>\n<reason>The new lemma is correctly stated and proved, each of the three functions "
                "is positive and decreasing on the half-line, and every rewritten proof reaches its statement.</reason>")
    if label.startswith("Check S"):
        return ("<verdict>invalid</verdict>\n<reason>The inequality in the proposition goes the other way: on $[k, k+1]$ the "
                "value at the left end is the largest, not the smallest, so the argument of the lemma does not apply as stated.</reason>")
    if label.startswith("Check proof of"):
        which = re.search(r"THE RESULT TO CHECK\n[^\n]*\[(res:\w+)\]", user)
        rid = which.group(1) if which else ""
        if rid == "res:R4" and SLIP[0] in user:
            return ("<verdict>problems</verdict>\n<issue>\nseverity: slip\nquote: " + SLIP[0] + "\n"
                    "problem: The integral of $1/x$ from $1$ to $n+1$ equals $\\log(n+1)$, not $\\log n$; with $\\log n$ the "
                    "display proves less than the statement.\ncorrection: " + SLIP[1] + "\n</issue>\n"
                    "<summary>The argument is right; the value of the integral is miscopied.</summary>")
        if rid == "res:R3":
            return ("<verdict>problems</verdict>\n<issue>\nseverity: gap\nquote: \\frac{1 - n^{1-s}}{s-1} \\le \\frac{1}{s-1}\n"
                    "problem: This inequality needs $s > 1$, which is not said at this point.\ncorrection: none\n</issue>\n"
                    "<summary>One step may need a hypothesis.</summary>")
        return "<verdict>correct</verdict>\n<summary>Every step follows; the computation was redone and agrees.</summary>"
    if label.startswith("Second opinion"):
        if "res:R4" in user.split("THE RESULT TO CHECK", 1)[-1].split("\n", 2)[1]:
            return ("<ruling>\nn: 1\ndecision: upheld\nreason: The integral equals $\\log(n+1) - \\log 1 = \\log(n+1)$.\n"
                    "correction: " + SLIP[1] + "\n</ruling>")
        return ("<ruling>\nn: 1\ndecision: dismissed\nreason: The theorem assumes $s > 1$ in its first sentence, so "
                "$n^{1-s} > 0$ and the step is immediate.\ncorrection: none\n</ruling>")
    if label.startswith("Referee report"):
        return r"""<summary>The paper gives elementary explicit bounds for the partial sums of the series of reciprocal powers. The arguments are short and, apart from one slip, correct. The presentation needs minor changes before publication.</summary>
<recommendation>minor revision</recommendation>
<point>
type: mathematics
where: res:R3
comment: The statement uses $\zeta(s)$, but the paper never says that $\zeta(s)$ is the limit of the sums $H_n^{(s)}$.
action: Say what $\zeta(s)$ is before it is used.
</point>
<point>
type: presentation
where: res:R1
comment: The proofs of the first two lemmas open with the same sentence.
action: Vary the wording.
</point>
<point>
type: typography
where: whole paper
comment: Several displayed formulas end without punctuation.
action: Punctuate every display as part of its sentence.
</point>
<point>
type: citation
where: Introduction
comment: The comparison of a sum with an integral is classical, yet it is used in the body without a reference.
action: Cite a standard source where the comparison is first used.
</point>"""
    # ---- completing a sketched proof (R2, when SCRIPTAVERA_MOCK_SKETCH=1)
    if label.startswith("Divide the sketch of"):
        return json.dumps({"steps": [
            {"quote": r"The function $x^{-1}$ is decreasing on $[k-1, k]$, so its smallest value on this interval is $k^{-1}$, and the interval has length $1$.",
             "claim": r"On $[k-1, k]$ the function $1/x$ is at least $1/k$.", "missing": r"why the smallest value is at $x = k$"},
            {"quote": r"Summing over $k = 2, \dots, n$ gives \[ \sum_{k=2}^{n} \frac{1}{k} \le \int_{1}^{n} \frac{dx}{x} = \log n \]",
             "claim": "The sum is at most the integral, which is $\\log n$.", "missing": "the value of the integral"},
            {"quote": r"Adding the term $k = 1$, which is $1$, gives the claim.", "claim": "The bound follows.", "missing": "none"}]})
    if label.startswith("Complete the sketch of"):
        first = "WHAT THE CHECKS FOUND" not in user
        extra = r" This is classical \cite{Unknown2020Invented}." if first else ""      # refused by the screen; the second try drops it
        return (r'<step n="1">Indeed, for $x \in [k-1, k]$ we have $x \le k$, hence $\frac{1}{x} \ge \frac{1}{k}$; integrating over an interval of length $1$ gives the inequality below.' + extra + "</step>\n"
                r'<step n="2">Here $\int_{1}^{n} \frac{dx}{x} = \log n - \log 1 = \log n$, since $\log 1 = 0$.</step>' + "\n"
                '<step n="3">NONE</step>')
    if label.startswith("Check the completion of"):
        n = re.findall(r"Addition (\d+) \(inserted", user)
        return "".join(f'<addition n="{k}">\nverdict: faithful\nreason: none\n</addition>\n' for k in n) + \
            "<proves>yes</proves>\n<reason>Each addition justifies its own step; together they prove the statement.</reason>"
    if label.startswith("Check the completed proof of"):
        return "<verdict>correct</verdict>\n<summary>Every step follows, including the added justifications.</summary>"
    if label.startswith("Apply correction"):
        return _passage(user, "PROOF\n").replace(*SLIP)
    if label.startswith("Apply your instruction"):
        return _passage(user, "PASSAGE\n") + " Here $\\zeta(s)$ denotes the limit of $H_n^{(s)}$ as $n \\to \\infty$."

    # ---- the final version
    if label.startswith("Copy-edit"):
        text = _passage(user, "THE PART TO EDIT\n\n")
        second = "(second try)" in label
        if "res:R5" in text:                       # an editor who keeps deleting a proof: both tries are refused
            return re.sub(r"\\begin\{proof\}.*?\\end\{proof\}", "", _polish(text), count=1, flags=re.S)
        if not second and r"H_n^{(2)} \le 2 - \frac{1}{n}" in text:
            return _polish(text).replace(r"H_n^{(2)} \le 2 - \frac{1}{n}", r"H_n^{(2)} < 2 - \frac{1}{n}")
        return _polish(text)
    if label.startswith("Verify citations"):
        rows = [f"{i} || {'partly' if i == 2 else 'supported'} || {'the notes mention this only briefly' if i == 2 else 'matches the reading notes'}"
                for i in (int(x) for x in re.findall(r"(?m)^(\d+)\. SENTENCE", user))]
        return "<checks>\n" + "\n".join(rows) + "\n</checks>"
    if label.startswith("Write abstract"):
        return ("<abstract>\nWe give explicit bounds for the partial sums $H_n^{(s)}$ of the series of reciprocal powers. "
                "Comparing each term with an integral of a decreasing function, we show that $H_n^{(s)} \\le s/(s-1)$ for "
                "every $s > 1$ and all $n \\ge 1$, and that $\\log(n+1) \\le H_n^{(1)} \\le 1 + \\log n$.\n</abstract>\n"
                "<keywords>partial sums, reciprocal powers, harmonic sums, integral comparison, explicit bounds, zeta function, "
                "elementary inequalities</keywords>\n<msc>26D15, 40A25, 11M06</msc>")
    if label.startswith("Fit abstract"):
        want = re.search(r"between (\d+) and (\d+) words|at most (\d+) words", system)
        if want and want.group(3) and int(want.group(3)) < 120:
            return "We give explicit bounds for the partial sums of the series of reciprocal powers by comparison with integrals."
        return LONG_ABSTRACT
    if label.startswith("Propose titles"):
        return ("<titles>\nFind explicit bounds for sums of reciprocal powers\n"
                "Explicit bounds for partial sums of reciprocal powers\nHow large are the harmonic sums?\n"
                "Integral comparison and the partial sums of the zeta series\n"
                "Elementary bounds for $H_n^{(s)}$\n</titles>")
    if label.startswith("Write the plain-language summary"):
        return ("The question\nHow large is the sum of the reciprocal powers $1 + 2^{-s} + \\dots + n^{-s}$?\n\nWhat was known\nThe infinite sum is classical.\n\n"
                "What this paper shows\nThe paper proves explicit bounds for every $n$.\n\nWhy it matters\nExplicit bounds are needed in estimates.")
    if label.startswith("Write the slides"):
        return ("\\begin{frame}{The question}\n\\begin{itemize}\n\\item Partial sums of reciprocal powers.\n\\end{itemize}\n\\end{frame}\n"
                "\\begin{frame}{A formula}\n\\[ x^{17} = 42 \\]\n\\end{frame}")
    if label.startswith("Write the poster"):
        return "\\section*{The question}\nPartial sums of reciprocal powers.\n\\section*{Results}\nExplicit bounds for every $n$."
    if label.startswith("Split the report into points"):
        if "PROOF CORRECTIONS" in user:
            return json.dumps({"points": [
                {"quote": "AQ1: Please confirm the affiliation of the author.", "request": "Confirm the affiliation.", "where": "", "type": "query"},
                {"quote": "Page 2: clarify the wording of the first sentence of the introduction.", "request": "clarify the wording", "where": "Section 1", "type": "language"}]})
        return json.dumps({"points": [
            {"quote": "The first sentence of Section 1 should be clarified.", "request": "clarify the wording", "where": "Section 1", "type": "language"},
            {"quote": "In the main lemma, the bound should read x+ instead.", "request": "change a formula in the main lemma", "where": "Lemma 2.1", "type": "mathematics"},
            {"quote": "Is the constant optimal?", "request": "Say whether the constant is optimal.", "where": "", "type": "query"}]})
    if label.startswith("Write the letter"):
        pts = re.findall(r"^(P\d+) \[(\w+)\] COMMENT: (.*)$", user, re.M)
        return "Response to the referee\n\nWe thank the referee for the careful reading.\n\n" + "\n".join(
            f"{pid}. {c}\nResponse: done as described." for pid, _, c in pts)
    if label.startswith("Editor command"):
        return _editor_command(user, second="(second try)" in label)
    if label.startswith("Fix LaTeX"):
        return _passage(user, "FRAGMENT:\n")
    if label.startswith("Revise"):
        return _passage(user, "PASSAGE\n") + " (Revised as requested.)"
    return "<kind>content</kind>"


EDITOR_LOG: list[str] = []          # the instructions the editor's AI received, for the tests to inspect


def _editor_command(user: str, second: bool = False) -> str:
    """The editor's AI: a few instructions the tests give, answered as the real model would, with the operations of
    the format in prompts.EDIT_COMMAND (item operations and exact text replacements)."""
    instr = re.search(r"THE AUTHOR'S INSTRUCTION\n(.*?)\n\nOUTLINE", user, re.S).group(1).strip()
    EDITOR_LOG.append(instr)
    files = dict(re.findall(r"=== (\S+) ===\n(.*?)(?=\n\n=== |\n\n\(Not shown|\Z)", user.split("\nFILES\n", 1)[1], re.S))
    low = instr.lower()
    outline = re.search(r"OUTLINE \(numbers as printed in the PDF\)\n(.*?)\n\nWHERE", user, re.S).group(1)
    if low.endswith("?"):
        used = re.findall(r"(Theorem|Lemma|Definition|Corollary) ([\d.]+)", outline)
        return f"<answer>The paper has {len(used)} numbered statements; Lemma 2.3 is used in the proof of Theorem 3.1.</answer>"
    if "fix the latex error" in low:
        m = re.search(r"reported at (\S+) line (\d+)", instr)
        path, line = m.group(1), int(m.group(2))
        bad = files[path].split("\n")[line - 1]
        cmd = re.search(r"\\(notacommand)\b", bad)
        fixed = bad.replace("\\notacommand", "\\mathbb{Z}") if cmd else bad
        return (f"<summary>Replaced the undefined command \\notacommand by \\mathbb{{Z}}.</summary>\n<edit file=\"{path}\">\n<find>{bad}</find>\n"
                f"<replace>{fixed}</replace>\n</edit>\n<note>Check that $\\mathbb{{Z}}$ is what you meant.</note>")
    if "more detailed" in low:
        m = re.search(r"proof of (\w+ [\d.]+)", instr, re.I)
        target = m.group(1)
        if not second and "bad find" in low:
            return "<summary>x</summary>\n<edit file=\"main.tex\">\n<find>text that is nowhere</find>\n<replace>y</replace>\n</edit>"
        return (f"<summary>Wrote the proof of {target} in more detail.</summary>\n<replace item=\"the proof of {target}\">\\begin{{proof}}\n"
                "We argue step by step. First, every term is positive, so the sum increases with $n$.\n"
                "Second, each term is at most the integral over the interval before it, and adding these gives the claim.\n"
                "\\end{proof}</replace>")
    if "rewrite the sentences that refer to it" in low:
        m = re.search(r"remove (\w+ [\d.]+)", instr, re.I)
        target = m.group(1)
        lab = re.search(re.escape(target) + r"[^\n]*\[label ([^\]]+)\]", outline)
        ops = [f"<delete item=\"{target}\"/>"]
        if lab:
            for path, text in files.items():
                for line in text.split("\n"):
                    if "\\ref{" + lab.group(1) + "}" in line:
                        new = re.sub(r"[^.]*\\ref\{" + re.escape(lab.group(1)) + r"\}[^.]*\.\s*", "", line)
                        ops.append(f"<edit file=\"{path}\">\n<find>{line}</find>\n<replace>{new}</replace>\n</edit>")
        return f"<summary>Removed {target} and the sentence that referred to it.</summary>\n" + "\n".join(ops)
    if "clarify the wording" in low:
        path = next(k for k in sorted(files) if k.startswith("sections/"))
        line = next(l for l in files[path].split("\n") if l.strip() and "$" not in l and not l.lstrip().startswith("\\"))
        return (f"<summary>Clarified the wording of a sentence.</summary>\n<edit file=\"{path}\">\n<find>{line}</find>\n"
                f"<replace>{line.rstrip()} Here we make this precise.</replace>\n</edit>")
    if "change a formula" in low:
        path = next(k for k in files if k.startswith("sections/") and "$" in files[k])
        line = next(l for l in files[path].split("\n") if "$" in l)
        return (f"<summary>Changed a formula.</summary>\n<edit file=\"{path}\">\n<find>{line}</find>\n"
                f"<replace>{line.replace('$', '$x+', 1)}</replace>\n</edit>")
    return "<question>Which statement do you mean? Give its number as printed in the PDF.</question>"
