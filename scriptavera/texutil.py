# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""LaTeX text utilities: splitting text from mathematics, cleaning model output,
converting Unicode, and escaping metadata."""

from __future__ import annotations

import re
import unicodedata

DISPLAY_ENVS = ("equation", "equation*", "align", "align*", "gather", "gather*", "multline", "multline*",
                "eqnarray", "eqnarray*", "flalign", "flalign*", "alignat", "alignat*", "displaymath")

_ENV_ALT = "|".join(re.escape(e) for e in sorted(DISPLAY_ENVS, key=len, reverse=True))
_MATH_RE = re.compile(
    r"(?P<dd>\$\$.*?\$\$)"
    r"|(?P<br>\\\[.*?\\\])"
    r"|(?P<env>\\begin\{(?P<envname>" + _ENV_ALT + r")\}.*?\\end\{(?P=envname)\})"
    r"|(?P<pa>\\\(.*?\\\))"
    r"|(?P<in>(?<![\\$])\$(?!\$)(?:\\.|[^$\\])+?\$)",
    re.S)


def _group_end(s: str, i: int) -> int:
    """s[i] is '{': the index just after its matching '}', or -1."""
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{" and (j == 0 or s[j - 1] != "\\"):
            depth += 1
        elif s[j] == "}" and (j == 0 or s[j - 1] != "\\"):
            depth -= 1
            if depth == 0:
                return j + 1
    return -1


_BRACE_CMD = re.compile(r"\\(?:underbrace|overbrace)\s*\{")


def drop_brace_labels(s: str) -> str:
    """\\underbrace{x}_{label} -> x and \\overbrace{x}^{label} -> x: the author's labels on a formula are not
    part of the formula. (\\underset and \\overset are left alone: they usually carry mathematics.)"""
    s = s or ""
    for _ in range(50):
        m = _BRACE_CMD.search(s)
        if not m:
            return s
        a = m.end() - 1
        b = _group_end(s, a)
        if b < 0:
            return s
        inner = s[a + 1:b - 1]
        k = b
        while k < len(s) and s[k].isspace():
            k += 1
        end = b
        if k < len(s) and s[k] in "_^":
            k += 1
            while k < len(s) and s[k].isspace():
                k += 1
            if k < len(s) and s[k] == "{":
                c = _group_end(s, k)
                end = c if c > 0 else b
            elif k < len(s):
                end = k + 1
        s = s[:m.start()] + inner + s[end:]
    return s


def split_math(text: str) -> list[tuple[str, str]]:
    """Split into ('text'|'inline'|'display'|'env', content). For 'inline' and
    'display' the content excludes delimiters; for 'env' it is the full environment."""
    out: list[tuple[str, str]] = []
    pos = 0
    for m in _MATH_RE.finditer(text or ""):
        if m.start() > pos:
            out.append(("text", text[pos:m.start()]))
        if m.group("dd"):
            out.append(("display", m.group("dd")[2:-2]))
        elif m.group("br"):
            out.append(("display", m.group("br")[2:-2]))
        elif m.group("env"):
            out.append(("env", m.group("env")))
        elif m.group("pa"):
            out.append(("inline", m.group("pa")[2:-2]))
        else:
            out.append(("inline", m.group("in")[1:-1]))
        pos = m.end()
    if pos < len(text or ""):
        out.append(("text", text[pos:]))
    return out


def join_math(parts: list[tuple[str, str]]) -> str:
    out = []
    for kind, c in parts:
        if kind == "text" or kind == "env":
            out.append(c)
        elif kind == "inline":
            out.append(f"${c}$")
        else:
            out.append(f"\\[{c}\\]")
    return "".join(out)


def find_group(s: str, i: int) -> tuple[str, int] | None:
    """s[i] must be '{'. Returns (content, index after the closing brace)."""
    if i >= len(s) or s[i] != "{":
        return None
    depth = 0
    j = i
    while j < len(s):
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1:j], j + 1
        j += 1
    return None


# --------------------------------------------------------------- Unicode

_UNI_MATH = {
    "≤": r"\le", "≥": r"\ge", "≠": r"\ne", "∈": r"\in", "∉": r"\notin", "⊂": r"\subset", "⊆": r"\subseteq",
    "⊃": r"\supset", "⊇": r"\supseteq", "∩": r"\cap", "∪": r"\cup", "→": r"\to", "←": r"\leftarrow",
    "↦": r"\mapsto", "⇒": r"\implies", "⟹": r"\implies", "⇔": r"\iff", "⟺": r"\iff", "×": r"\times",
    "·": r"\cdot", "⋅": r"\cdot", "∞": r"\infty", "∑": r"\sum", "∏": r"\prod", "∫": r"\int", "∀": r"\forall",
    "∃": r"\exists", "∅": r"\emptyset", "ℤ": r"\mathbb{Z}", "ℚ": r"\mathbb{Q}", "ℝ": r"\mathbb{R}",
    "ℂ": r"\mathbb{C}", "ℕ": r"\mathbb{N}", "𝔽": r"\mathbb{F}", "≅": r"\cong", "≈": r"\approx",
    "≡": r"\equiv", "∼": r"\sim", "∣": r"\mid", "∤": r"\nmid", "⟨": r"\langle", "⟩": r"\rangle",
    "⊲": r"\triangleleft", "⊴": r"\trianglelefteq", "⊳": r"\triangleright", "∴": r"\therefore",
    "∵": r"\because", "±": r"\pm", "∓": r"\mp", "÷": r"\div", "√": r"\sqrt", "∂": r"\partial",
    "∇": r"\nabla", "⊕": r"\oplus", "⊗": r"\otimes", "∘": r"\circ", "∖": r"\setminus", "≪": r"\ll",
    "≫": r"\gg", "⌊": r"\lfloor", "⌋": r"\rfloor", "⌈": r"\lceil", "⌉": r"\rceil", "∎": r"\qed",
    "¬": r"\neg", "∧": r"\wedge", "∨": r"\vee", "⊥": r"\perp", "∥": r"\parallel", "≺": r"\prec",
    "≼": r"\preceq", "⊢": r"\vdash", "⊨": r"\models", "ℓ": r"\ell", "ℵ": r"\aleph", "′": "'",
    "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta", "ε": r"\varepsilon", "ϵ": r"\epsilon",
    "ζ": r"\zeta", "η": r"\eta", "θ": r"\theta", "ι": r"\iota", "κ": r"\kappa", "λ": r"\lambda", "μ": r"\mu",
    "ν": r"\nu", "ξ": r"\xi", "π": r"\pi", "ρ": r"\rho", "σ": r"\sigma", "τ": r"\tau", "υ": r"\upsilon",
    "φ": r"\varphi", "ϕ": r"\phi", "χ": r"\chi", "ψ": r"\psi", "ω": r"\omega", "Γ": r"\Gamma",
    "Δ": r"\Delta", "Θ": r"\Theta", "Λ": r"\Lambda", "Ξ": r"\Xi", "Π": r"\Pi", "Σ": r"\Sigma",
    "Φ": r"\Phi", "Ψ": r"\Psi", "Ω": r"\Omega",
}
_UNI_TEXT = {"“": "``", "”": "''", "‘": "`", "’": "'", "–": "--", "—": "---", "…": r"\ldots{}",
             "\u00a0": "~", "\u2009": r"\,", "\u202f": r"\,", "\u2212": "-", "\ufeff": "", "\u200b": ""}
_SUPER = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻ⁿ", "0123456789+-n")
_SUB = str.maketrans("₀₁₂₃₄₅₆₇₈₉₊₋", "0123456789+-")


def _uni_in_math(s: str) -> str:
    s = s.replace("\u2212", "-")
    s = re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻ⁿ]+", lambda m: "^{" + m.group(0).translate(_SUPER) + "}", s)
    s = re.sub(r"[₀₁₂₃₄₅₆₇₈₉₊₋]+", lambda m: "_{" + m.group(0).translate(_SUB) + "}", s)
    out = []
    for i, ch in enumerate(s):
        rep = _UNI_MATH.get(ch)
        if rep is None:
            out.append(_UNI_TEXT.get(ch, ch) if ch in ("\u00a0", "\u2009", "\u202f", "\ufeff", "\u200b") else ch)
        else:
            nxt = s[i + 1] if i + 1 < len(s) else ""
            out.append(rep + (" " if rep.startswith("\\") and nxt.isalpha() else ""))
    return "".join(out)


def _uni_in_text(s: str) -> str:
    for k, v in _UNI_TEXT.items():
        s = s.replace(k, v)
    s = re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻ⁿ]+", lambda m: "$^{" + m.group(0).translate(_SUPER) + "}$", s)
    s = re.sub(r"[₀₁₂₃₄₅₆₇₈₉₊₋]+", lambda m: "$_{" + m.group(0).translate(_SUB) + "}$", s)
    # a run of math characters in running text becomes one inline formula
    pattern = "[" + "".join(re.escape(c) for c in _UNI_MATH) + "]+"
    return re.sub(pattern, lambda m: "$" + _uni_in_math(m.group(0)).strip() + "$", s)


def unicode_to_latex(text: str) -> str:
    """Replace Unicode mathematics and typography by LaTeX commands."""
    parts = []
    for kind, c in split_math(text):
        if kind == "text":
            parts.append((kind, _uni_in_text(c)))
        else:
            parts.append((kind, _uni_in_math(c)))
    return join_math(parts)


_ACCENTS = {"\u0301": "'", "\u0300": "`", "\u0302": "^", "\u0308": '"', "\u0303": "~", "\u0304": "=",
            "\u0306": "u", "\u030c": "v", "\u0327": "c", "\u0326": "c", "\u0328": "k", "\u030a": "r",
            "\u0307": ".", "\u030b": "H", "\u0323": "d"}
_SPECIAL = {"ø": r"{\o}", "Ø": r"{\O}", "ł": r"{\l}", "Ł": r"{\L}", "ß": r"{\ss}", "æ": r"{\ae}", "Æ": r"{\AE}",
            "œ": r"{\oe}", "Œ": r"{\OE}", "đ": r"{\dj}", "Đ": r"{\DJ}", "ı": r"{\i}", "å": r"{\aa}", "Å": r"{\AA}"}


_TEX_ACCENT = re.compile(r"\\(?:['\"`^~=.]\s*\{?\\?[A-Za-z]\}?|[uvHckrd]\s*\{\\?[A-Za-z]\}|[uvHckrd] [A-Za-z]|(?:ss|ae|AE|oe|OE|aa|AA|o|O|l|L|i|j)(?![A-Za-z]))")


def latex_escape(s: str, keep_math: bool = True) -> str:
    """Escape bibliographic metadata (names, titles, venues) for LaTeX.

    Titles arrive in many dialects: plain Unicode, TeX accents (K\"ahler), BibTeX protective braces
    ({B}anach), inline $math$. Accent commands and inline math are kept; everything else is made literal."""
    def esc(t: str) -> str:
        kept: list[str] = []

        def keep(m: re.Match) -> str:
            kept.append(m.group(0))
            return f"\x00{len(kept) - 1}\x00"

        t = _TEX_ACCENT.sub(keep, t)
        depth, balanced = 0, True
        for ch in t:
            depth += (ch == "{") - (ch == "}")
            if depth < 0:
                balanced = False
                break
        if balanced and depth == 0:
            t = t.replace("{", "").replace("}", "")           # BibTeX protective braces carry no meaning here
        t = t.replace("\\", "\x01")
        for ch in "&%#_$":
            t = t.replace(ch, "\\" + ch)
        t = t.replace("{", r"\{").replace("}", r"\}").replace("~", r"\textasciitilde{}").replace("^", r"\^{}")
        t = t.replace("\x01", r"\textbackslash{}")
        out = []
        for ch in unicodedata.normalize("NFC", t):
            if ord(ch) < 128:
                out.append(ch)
            elif ch in _SPECIAL:
                out.append(_SPECIAL[ch])
            elif ch in _UNI_TEXT:
                out.append(_UNI_TEXT[ch])
            elif ch in _UNI_MATH:
                out.append("$" + _UNI_MATH[ch] + "$")
            else:
                d = unicodedata.normalize("NFD", ch)
                if len(d) == 2 and d[1] in _ACCENTS and ord(d[0]) < 128:
                    base = {"i": r"\i", "j": r"\j"}.get(d[0], d[0]) if _ACCENTS[d[1]] not in "ckd" else d[0]
                    out.append("{\\" + _ACCENTS[d[1]] + "{" + base + "}}")
                else:
                    folded = "".join(c for c in unicodedata.normalize("NFKD", ch) if ord(c) < 128)
                    out.append(folded)
        res = "".join(out)
        return re.sub(r"\x00(\d+)\x00", lambda m: "{" + kept[int(m.group(1))] + "}", res)

    s = s or ""
    if not keep_math or "$" not in s:
        return esc(s)
    out = []
    for kind, c in split_math(s):
        out.append(f"${_uni_in_math(c)}$" if kind == "inline" else esc(c))
    return "".join(out)


def escape_specials(text: str, underscore: bool = True) -> str:
    """Escape & % # (and _) that sit unescaped in running text. Mathematics is left alone."""
    parts = []
    for kind, c in split_math(text or ""):
        if kind == "text":
            c = re.sub(r"(?<!\\)([&%#])", r"\\\1", c)
            if underscore:
                c = re.sub(r"(?<!\\)_", r"\\_", c)
        parts.append((kind, c))
    return join_math(parts)


def escape_percent(text: str) -> str:
    """A bare % in a typeset passage would silently comment out the rest of its line."""
    return re.sub(r"(?<!\\)%", r"\\%", text or "")


# ------------------------------------------------------- cleaning output

def clean_fragment(text: str) -> str:
    """Normalise a LaTeX fragment written by a model."""
    t = (text or "").strip()
    t = re.sub(r"^```[a-zA-Z]*\s*\n", "", t)
    t = re.sub(r"\n```\s*$", "", t)
    if "\\begin{document}" in t:
        t = t.split("\\begin{document}", 1)[1]
    t = t.split("\\end{document}", 1)[0]
    t = re.sub(r"\\(maketitle|tableofcontents|newpage|clearpage|bigskip|medskip|smallskip|noindent)\b\s*", "", t)
    t = re.sub(r"\\(title|author|date)\s*\{[^{}]*\}\s*", "", t)
    t = t.replace("⟦math⟧", "")
    # $$...$$ is plain-TeX; LaTeX wants \[...\]
    t = re.sub(r"\$\$(.+?)\$\$", lambda m: "\\[" + m.group(1).strip() + "\\]", t, flags=re.S)
    # markdown that sometimes leaks into model output
    parts = []
    for kind, c in split_math(t):
        if kind == "text":
            c = re.sub(r"\*\*([^*\n]+)\*\*", r"\\textbf{\1}", c)
            c = re.sub(r"(?m)^#{1,2}\s+(.+)$", r"\\section{\1}", c)
            c = re.sub(r"(?m)^#{3,}\s+(.+)$", r"\\subsection{\1}", c)
        parts.append((kind, c))
    t = unicode_to_latex(join_math(parts))
    # an aligned block left bare inside \[ \] is fine; a bare align inside \[ \] is not
    t = re.sub(r"\\\[\s*(\\begin\{(align\*?|gather\*?|equation\*?)\}.*?\\end\{\2\})\s*\\\]", r"\1", t, flags=re.S)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


_SECTIONS = r"uncertain|changes|symbols|terms|gist|kind|flags"
_LOOP = re.compile(r"((?:\\[A-Za-z]+|[A-Za-z][^\s\\$]{0,19})[ \t]*)\1{24,}")
_MATH_OR_MARKUP = re.compile(r"(\$\$.+?\$\$|\$[^$\n]+?\$)|(<br\s*/?>)|(</?(?:u|b|i|em|strong|mark|span|font|p|div|small|big|"
                             r"transcript|" + _SECTIONS + r")\b[^<>$]{0,80}>)", re.S | re.I)


def answer_transcript(answer: str) -> str:
    """The transcription in a model's answer, however its tags came out. A reading started twice gives the last start;
    a missing opening tag gives everything before the closing one; what follows the transcription (doubts, changes,
    symbols) never comes with it. The same repair applies to a reading an earlier version saved with these parts."""
    t = answer or ""
    t = re.sub(r"<kind>.*?</kind>", "", t, flags=re.S | re.I)          # the notation survey puts its <kind> first
    close = re.search(r"</transcript\s*>", t, re.I)
    if close:
        t = t[:close.start()]
    opens = list(re.finditer(r"<transcript\b[^>]*>", t, re.I))
    if opens:
        t = t[opens[-1].end():]
    later = re.search(rf"<(?:{_SECTIONS})\b[^>]*>", t, re.I)
    if later:
        t = t[:later.start()]
    return t.strip()


def looping(text: str) -> bool:
    """A reading in which one command or word repeats endlessly ("\\quad \\quad \\quad ..."): the model broke down."""
    return bool(_LOOP.search(text or ""))


def collapse_loops(text: str) -> str:
    return _LOOP.sub(lambda m: m.group(1), text or "")


def clean_transcript(text: str) -> str:
    """Normalise a page transcription (markdown-style with $ and $$ mathematics). Markup a model may add outside the
    formulas (HTML such as <u>Def</u>, or a stray tag of the answer format) is removed; the words stay."""
    t = (text or "").strip()
    t = _MATH_OR_MARKUP.sub(lambda m: m.group(1) if m.group(1) else ("\n" if m.group(2) else ""), t)
    t = re.sub(r"^```[a-zA-Z]*\s*\n", "", t)
    t = re.sub(r"\n```\s*$", "", t)
    t = re.sub(r"(?<!\\)\\\[(.+?)\\\]", lambda m: "$$" + m.group(1).strip() + "$$", t, flags=re.S)   # not the \\[2pt] row spacer
    t = re.sub(r"(?<!\\)\\\((.+?)\\\)", lambda m: "$" + m.group(1).strip() + "$", t, flags=re.S)
    t = re.sub(r"\\begin\{(align\*?|gather\*?|equation\*?)\}(.+?)\\end\{\1\}",
               lambda m: "$$\\begin{aligned}" + m.group(2).strip() + "\\end{aligned}$$"
               if "$$" not in m.group(0) else m.group(0), t, flags=re.S)
    t = re.sub(r"\\(newpage|vspace\{[^{}]*\}|hspace\{[^{}]*\}|noindent|medskip|bigskip)\s*", "", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def check_balance(text: str) -> list[str]:
    """Cheap syntax checks that catch most compile failures."""
    problems = []
    body = re.sub(r"(?<!\\)%.*", "", text or "")
    depth = 0
    i = 0
    while i < len(body):
        c = body[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth < 0:
                problems.append("an extra closing brace }")
                depth = 0
        i += 1
    if depth > 0:
        problems.append(f"{depth} unclosed brace(s) {{")
    stripped = re.sub(r"\\\$", "", body)
    stripped = re.sub(r"\$\$", "", stripped)
    if stripped.count("$") % 2:
        problems.append("an unmatched $ sign")
    stack: list[str] = []
    for m in re.finditer(r"\\(begin|end)\{([^{}]+)\}", body):
        if m.group(1) == "begin":
            stack.append(m.group(2))
        elif stack and stack[-1] == m.group(2):
            stack.pop()
        else:
            problems.append(f"\\end{{{m.group(2)}}} without a matching \\begin")
    for env in stack:
        problems.append(f"\\begin{{{env}}} is never closed")
    if body.count("\\[") != body.count("\\]"):
        problems.append("unbalanced \\[ ... \\]")
    if re.search(r"\\left\b", body) and len(re.findall(r"\\left\b", body)) != len(re.findall(r"\\right\b", body)):
        problems.append("\\left and \\right do not pair up")
    return problems


# ------------------------------------------------------------- typography
# What a referee marks with a pencil and no copy-editor should have to be asked about: the full stop after a
# displayed formula, the tie before a reference, a doubled word, a space before a comma. Done by rule, never by
# a model, and never touching a symbol: inside a formula only its final punctuation mark is ever added.

_TAIL = re.compile(r"(?:\s|\\\\(?:\[[^\]]*\])?|\\(?:qedhere|nonumber|notag)\b|\\label\{[^{}]*\}|\\tag\*?\{[^{}]*\})*$")
_INNER_END = re.compile(r"\\end\{(aligned|alignedat|split|gathered|align\*?|gather\*?|multline\*?|equation\*?|"
                        r"flalign\*?|alignat\*?|eqnarray\*?|displaymath)\}$")
_BLOCK_START = re.compile(r"\\end\{|\\item\b|\\(?:sub)*section\b|\\insertfigure\b|\\qed\b|\\begin\{(?:proof|lemma|theorem|"
                          r"proposition|corollary|definition|remark|example|claim|figurenote|itemize|enumerate)\}")
_PROTECT = re.compile(r"\\(?:eq)?ref\s*\{[^{}]*\}|\\cite[tp]?\s*(?:\[[^\]]*\])?\s*\{[^{}]*\}|\\label\s*\{[^{}]*\}|"
                      r"\\insertfigure\s*\{[^{}]*\}|\\url\s*\{[^{}]*\}|\\href\s*\{[^{}]*\}\s*\{[^{}]*\}|"
                      r"\\(?:begin|end)\s*\{[^{}]*\}(?:\[[^\]]*\])?|\\item\s*\[[^\]]*\]|(?<!\\)%[^\n]*")
_NAMED = "Lemma|Theorem|Proposition|Corollary|Definition|Remark|Example|Section|Figure|Claim|Equation|Conjecture|Table"
_SMALL = "the|a|an|of|in|to|is|and|we|for|with|by|on|as|are|be|it|if"


def _stop_display(c: str, mark: str = ".") -> str | None:
    """A displayed formula with `mark` after its last symbol, or None if it already ends with punctuation."""
    tail = _TAIL.search(c).group(0)
    core = c[:len(c) - len(tail)]
    if not core.strip():
        return None
    inner = _INNER_END.search(core)
    if inner:                                   # \end{aligned}: the mark belongs to the last line, inside
        done = _stop_display(core[:inner.start()], mark)
        return None if done is None else done + core[inner.start():] + tail
    last = core.rstrip()
    if not last.endswith("\\right.") and re.search(r"[.,;:]\}*$", last):
        return None                             # punctuated already
    return last + mark + core[len(last):] + tail


def _sentence_ends_here(after: str | None) -> bool:
    """Does the sentence end with the displayed formula that this text follows?"""
    if after is None:
        return True
    if re.match(r"[ \t]*\n\s*\n", after):
        return True
    t = after.lstrip()
    if not t or _BLOCK_START.match(t):
        return True
    return bool(re.match(r"[A-Z][a-z]+\b|[A-Z]\s|\x00K", t))


def tidy(text: str) -> tuple[str, dict]:
    """Apply the typographical rules to one passage. Returns the passage and a count of each kind of correction."""
    counts = {"stops": 0, "ties": 0, "spacing": 0, "doubled": 0, "abbreviations": 0, "capitals": 0, "quotes": 0}
    kept: list[str] = []

    def protect(m: re.Match) -> str:
        s = m.group(0)
        kind = "R" if re.match(r"\\(?:eq)?ref", s) else ("C" if s.startswith("\\cite") else "K")
        kept.append(s)
        return f"\x00{kind}{len(kept) - 1}\x00"

    parts = split_math(text or "")
    parts = [(k, _PROTECT.sub(protect, c) if k == "text" else c) for k, c in parts]

    # 1. displayed formulas carry the punctuation of their sentence
    for i, (kind, c) in enumerate(parts):
        if kind not in ("display", "env"):
            continue
        after = parts[i + 1][1] if i + 1 < len(parts) and parts[i + 1][0] == "text" else (None if i + 1 >= len(parts) else "")
        moved = re.match(r"[ \t]*\n?[ \t]*([.,;:])(?=\s|$)", after or "")
        if moved:                               # "\] ." or "\] ," : the mark goes inside the display
            done = _stop_display(c, moved.group(1))
            parts[i + 1] = ("text", after[moved.end():])
            if done is not None:
                parts[i] = (kind, done)
            counts["stops"] += 1
            continue
        if _sentence_ends_here(after):
            done = _stop_display(c)
            if done is not None:
                parts[i] = (kind, done)
                counts["stops"] += 1

    # 2. running text
    for i, (kind, c) in enumerate(parts):
        if kind != "text":
            continue
        prev_inline = i > 0 and parts[i - 1][0] == "inline"

        def sub(pat: str, rep, key: str, s: str, flags: int = 0) -> str:
            new, n = re.subn(pat, rep, s, flags=flags)
            counts[key] += n
            return new

        if prev_inline:
            c = sub(r"^[ \t]+(?=[,.;:](?:\s|$))", "", "spacing", c)
        c = sub(r"(?<=[A-Za-z0-9)\]}\x00])[ \t]+(?=[,.;:](?:\s|$))", "", "spacing", c)
        c = sub(r"(?<=\S)[ \t]{2,}(?=\S)", " ", "spacing", c)
        c = sub(rf"\b({_NAMED})(s?)(?:[ \t]+|~?\n[ \t]*)(?=\x00R)", r"\1\2~", "ties", c)
        c = sub(rf"\b({_NAMED.lower()})(s?)~?(?=\x00R)", lambda m: m.group(1).capitalize() + m.group(2) + "~", "capitals", c)
        c = sub(r"(?<=[A-Za-z0-9,)])[ \t]+(?=\x00C)", "~", "ties", c)
        c = sub(r"\b(i\.e\.|e\.g\.|cf\.|resp\.|viz\.|et al\.|vs\.)[ \t]+(?=[a-z$(\x00])", r"\1\\ ", "abbreviations", c)
        c = sub(rf"\b({_SMALL})[ \t]+\1\b", r"\1", "doubled", c, re.I)
        c = sub(r"(?<![.\\])\.\.\.(?!\.)", r"\\ldots{}", "spacing", c)
        c = sub(r'(?<![\\A-Za-z])"([^"\n]{1,160}?)(?<!\\)"', r"``\1''", "quotes", c)
        parts[i] = (kind, c)
    out = join_math(parts)
    out = re.sub(r"\x00[RCK](\d+)\x00", lambda m: kept[int(m.group(1))], out)
    return out, counts


def close_paragraphs(text: str) -> tuple[str, int]:
    """A paragraph of prose that ends on a word, a formula or a reference gets its full stop."""
    n = 0
    paras = re.split(r"(\n\s*\n)", text or "")
    for i in range(0, len(paras), 2):
        para = paras[i].rstrip()
        if not para or re.search(r"[.:;,!?][)}'’\]]*$", para):
            continue                            # closed already, or leading into a list or a display
        if re.search(r"\\(?:end|begin|label|insertfigure)\s*\{[^{}]*\}$|\\\]$|\\\\$|\$\$$|\\[A-Za-z]+$", para):
            continue                            # ends with an environment, a display, a line break or a bare command
        if re.match(r"\s*\\(?:sub)*section", para):
            continue
        if re.search(r"[A-Za-z0-9)\]$}]$", para):
            paras[i] = para + "." + paras[i][len(para):]
            n += 1
    return "".join(paras), n


# A model sometimes writes its own thinking into its answer ("Wait, let's check page 3 ..."), or quotes the
# instructions it was given. None of that may reach the paper.
_LEAK = re.compile(
    r"\b(?:Wait[,!.?:]|Hmm+[,.!]|Let's (?:check|look|see|verify|re-?read)|Let me (?:check|look|see|re-?read|verify)|"
    r"Actually,? wait|I (?:need to|should|will now|think)\b|Okay,? (?:so|let's))"
    r"|FRAGMENT TO CORRECT|ORIGINAL TRANSCRIPT|FORMULAS TO RESTORE|currently typeset as|THE PART TO EDIT"
    r"|NOTES FOR THIS SECTION|WRITE THIS SECTION|CONFIRMED NOTATION|\bDOSSIER\b|character for character")


def leaks(text: str) -> bool:
    """Does a model's answer contain its own reasoning or the instructions it was given?"""
    return bool(_LEAK.search(text or ""))


def drop_leaks(text: str) -> tuple[str, int]:
    """Remove the paragraphs that are a model's reasoning rather than paper text. Returns the text and the number
    of paragraphs removed."""
    paras = re.split(r"\n\s*\n", text or "")
    kept = [x for x in paras if not leaks(x)]
    return "\n\n".join(kept), len(paras) - len(kept)
