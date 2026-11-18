# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Which commands and environments LaTeX knows in a project: those of LaTeX itself, of the document class and of every
package the project loads (each read from the LaTeX installed on this computer, found with kpsewhich, and the files
they load in turn), those the project defines itself, and those the last compile used without complaint.

The editor uses this to say, while the author types, that a command is not one LaTeX knows ("\\textbff: did you mean
\\textbf?"), before compiling. When a package cannot be found, `complete` is false and the editor only points out
commands that look like a misspelling of a known one."""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

# TeX's own commands (TeX, e-TeX, pdfTeX, XeTeX and LuaTeX primitives a document may use): no file defines them.
PRIMITIVES = set("""
relax hbox vbox vtop hskip vskip kern penalty par noindent indent hfil hfill hfilneg hss vfil vfill vfilneg vss raise lower
moveleft moveright hrule vrule leaders cleaders xleaders the number romannumeral string meaning csname endcsname expandafter
noexpand edef def gdef xdef let futurelet global long outer protected if ifx ifnum ifdim ifodd ifvmode ifhmode ifmmode ifinner
ifvoid ifhbox ifvbox ifeof iftrue iffalse ifcase ifcat ifdefined ifcsname iffontchar else fi or unless afterassignment aftergroup
begingroup endgroup char chardef mathchar mathchardef count countdef dimen dimendef skip skipdef muskip muskipdef toks toksdef box
setbox copy unhbox unvbox unhcopy unvcopy wd ht dp advance multiply divide numexpr dimexpr glueexpr muexpr showthe show showbox
message errmessage write immediate openout closeout openin closein read readline input endinput jobname inputlineno catcode lccode
uccode sfcode mathcode delcode uppercase lowercase lastskip lastkern lastpenalty lastbox unskip unkern unpenalty vadjust insert mark
marks vsplit halign valign noalign omit span cr crcr tabskip hangindent hangafter parshape parindent parskip baselineskip lineskip
lineskiplimit leftskip rightskip hsize vsize textfont scriptfont scriptscriptfont fam mathsurround displaywidth displayindent
predisplaysize mathchoice mathaccent radical delimiter left right middle over atop above overwithdelims atopwithdelims
abovewithdelims mathord mathop mathbin mathrel mathopen mathclose mathpunct mathinner underline overline vcenter limits nolimits
displaylimits displaystyle textstyle scriptstyle scriptscriptstyle nonscript mskip mkern eqno leqno discretionary accent
ignorespaces dump end batchmode nonstopmode scrollmode errorstopmode tracingonline tracingmacros tracingcommands showlists
detokenize unexpanded scantokens interactionmode lastnodetype currentgrouplevel currentiftype showgroups showtokens everypar
everymath everydisplay everyhbox everyvbox everyjob everycr everyeof hyphenation patterns hyphenchar skewchar fontname font
nullfont topskip maxdepth splitmaxdepth boxmaxdepth emergencystretch tolerance pretolerance looseness hbadness vbadness hfuzz
vfuzz overfullrule widowpenalty clubpenalty brokenpenalty exhyphenpenalty hyphenpenalty linepenalty interlinepenalty
postdisplaypenalty predisplaypenalty displaywidowpenalty floatingpenalty insertpenalties outputpenalty output shipout deadcycles
pagegoal pagetotal pagedepth pagestretch pageshrink holdinginserts day month year time mag escapechar newlinechar endlinechar
spacefactor spaceskip xspaceskip uchyph lefthyphenmin righthyphenmin language setlanguage noboundary prevdepth prevgraf
badness parfillskip abovedisplayskip belowdisplayskip abovedisplayshortskip belowdisplayshortskip delimiterfactor
delimitershortfall nulldelimiterspace scriptspace thinmuskip medmuskip thickmuskip binoppenalty relpenalty predisplaydirection
showbox showboxbreadth showboxdepth errorcontextlines vfuzz lastlinefit savingvdiscards savinghyphcodes pagediscards
splitdiscards interlinepenalties clubpenalties widowpenalties displaywidowpenalties fontcharwd fontcharht fontchardp fontcharic
parshapelength parshapeindent parshapedimen gluestretch glueshrink gluestretchorder glueshrinkorder gluetomu mutoglue
eTeXversion eTeXrevision eTeXmode tracingassigns tracinggroups tracingifs tracingscantokens tracingnesting predisplaydirection
pdfoutput pdfstrcmp pdfpagewidth pdfpageheight pdfminorversion pdfcompresslevel pdfobjcompresslevel pdfinfo pdfcatalog pdfliteral
pdfsavepos pdflastxpos pdflastypos pdfrandomseed pdfsetrandomseed pdfuniformdeviate pdfnormaldeviate pdfmdfivesum pdffilesize
pdffilemoddate pdfelapsedtime pdfresettimer pdfprimitive pdfshellescape pdftexversion pdftexrevision pdftexbanner pdfcreationdate
pdfescapestring pdfescapename pdfescapehex pdfunescapehex pdffiledump pdfmatch pdflastmatch pdfximage pdfrefximage
pdflastximage pdfxform pdfrefxform pdflastxform pdfobj pdfrefobj pdflastobj pdfannot pdfdest pdfoutline pdfstartlink pdfendlink
pdfpageattr pdfpagesattr pdfpageresources pdfhorigin pdfvorigin pdfpkresolution pdfdecimaldigits pdfgentounicode pdfglyphtounicode
pdfinclusionerrorlevel pdfsuppresswarningpagegroup pdfmapfile pdfmapline pdfadjustspacing pdfprotrudechars pdfcopyfont
pdfdraftmode pdfvariable efcode lpcode rpcode quitvmode vadjust ifpdfprimitive ifpdfabsnum ifpdfabsdim pdfstrcmp leftmarginkern
rightmarginkern letterspacefont pdftracingfonts pdfnoligatures expanded XeTeXversion XeTeXrevision directlua luaescapestring
latelua luatexversion luatexrevision luafunction Uchar Umathchar Umathcode Udelimiter Uradical Uroot Uoverdelimiter
Uunderdelimiter Udelcode Umathchardef Umathcharnumdef Uchar Ucharcat strcmp normalunexpanded mdfivesum uniformdeviate
""".split())

# a few commands LaTeX itself defines in ways the patterns below do not see, and those that come from TeX's plain format
EXTRA = set("""
bgroup egroup space empty relax z@ p@ m@ hb@xt@ mathcal mathrm mathit mathbf mathsf mathtt mathnormal makeatletter makeatother
alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu xi pi varpi rho varrho sigma varsigma tau
upsilon phi varphi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega textbf textit texttt textrm textsf textsc
textup textsl textmd textnormal emph item par newline linebreak pagebreak nolinebreak nopagebreak newpage clearpage cleardoublepage
""".split())

_DEF = re.compile(
    r"\\(?:(?:re)?newcommand|providecommand|DeclareRobustCommand|(?:re)?newrobustcmd|providerobustcmd|DeclareTextFontCommand|"
    r"DeclareOldFontCommand|DeclareMathOperator|DeclarePairedDelimiterX?|DeclarePairedDelimiterXPP|DeclareMathSymbol|DeclareMathDelimiter|"
    r"DeclareMathAccent|DeclareMathRadical|DeclareMathAlphabet|DeclareSymbolFontAlphabet|DeclareTextSymbol|DeclareTextCommand|"
    r"DeclareTextAccent|DeclareTextSymbolDefault|DeclareTextCommandDefault|DeclareTextAccentDefault|ProvideTextCommand|"
    r"ProvideTextCommandDefault|(?:New|Renew|Provide|Declare)(?:Expandable)?DocumentCommand|(?:New|Declare)CommandCopy|newfont|"
    r"newlength|newsavebox|newcount|newdimen|newskip|newmuskip|newtoks|newbox|newread|newwrite|newinsert|newfam|newlanguage|"
    r"chardef|mathchardef|countdef|dimendef|skipdef|toksdef|font|(?:[gex]?def)|let|futurelet|"
    r"cs_(?:new|g?set)(?:_protected)?(?:_nopar)?(?:_eq)?:[A-Za-z]*)\*?\s*\{?\s*\\([A-Za-z]+)(?![A-Za-z@_:])")
_NAMEDEF = re.compile(r"\\@namedef\s*\{([A-Za-z]+)\}|\\csname\s*([A-Za-z]+)\s*\\endcsname")
_NEWIF = re.compile(r"\\newif\s*\\if([A-Za-z]+)")
_COUNTER = re.compile(r"\\newcounter\s*\{([A-Za-z]+)\}")
_ENV = re.compile(r"\\(?:(?:re)?newenvironment|(?:New|Renew|Provide|Declare)DocumentEnvironment|newtheorem\*?|declaretheorem"
                  r"(?:\[[^\]]*\])?|newmdenv(?:\[[^\]]*\])?|newtcolorbox|NewEnviron|lstnewenvironment|newfloat|DeclareFloatingEnvironment"
                  r"(?:\[[^\]]*\])?)\s*\{([A-Za-z@*]+)\}")
_LOADS = re.compile(r"\\(?:usepackage|RequirePackage|RequirePackageWithOptions)\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}")
_CLASS = re.compile(r"\\(?:documentclass|LoadClass|LoadClassWithOptions)\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}")
_INPUT = re.compile(r"\\(?:input|InputIfFileExists|@input@?|pgfutil@InputIfFileExists|file_input:n|IfFileExists)\s*\{?\s*"
                    r"([A-Za-z0-9][A-Za-z0-9._\-]*\.(?:tex|def|cfg|clo|sty|ltx|code\.tex))\b")
_COMMENT = re.compile(r"(?<!\\)%.*")

KERNEL = ["latex.ltx", "fonttext.ltx", "fontmath.ltx", "ot1enc.def", "omlenc.def", "omsenc.def", "t1enc.def", "ts1enc.def",
          "omxenc.def", "ltxcmds.sty"]
MAX_FILES = 450
MAX_BYTES = 40_000_000

_cache: dict[tuple, dict] = {}
_guard = threading.Lock()


def _kpsewhich() -> str | None:
    return shutil.which("kpsewhich")


def _find(names: list[str]) -> dict[str, Path]:
    """Where the LaTeX installed here keeps each of these files (those it has)."""
    exe = _kpsewhich()
    out: dict[str, Path] = {}
    if not exe or not names:
        return out
    for i in range(0, len(names), 60):
        chunk = names[i:i + 60]
        try:
            r = subprocess.run([exe, *chunk], capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            return out
        for line in (r.stdout or b"").decode("utf-8", "replace").splitlines():
            p = Path(line.strip())
            if p.name in chunk and p.is_file():
                out[p.name] = p
    return out


def scan_text(text: str, cmds: set[str], envs: set[str]) -> tuple[set[str], set[str]]:
    """Read the definitions in one file: the commands and environments it defines, and the packages, classes and
    files it loads (returned as file names)."""
    t = _COMMENT.sub("", text)
    for m in _DEF.finditer(t):
        cmds.add(m.group(1))
    for m in _NAMEDEF.finditer(t):
        cmds.add(m.group(1) or m.group(2))
    for m in _NEWIF.finditer(t):
        cmds.update({"if" + m.group(1), m.group(1) + "true", m.group(1) + "false"})
    for m in _COUNTER.finditer(t):
        cmds.add("the" + m.group(1))
    for m in _ENV.finditer(t):
        envs.add(m.group(1))
    loads: set[str] = set()
    for m in _LOADS.finditer(t):
        for name in m.group(1).split(","):
            name = name.strip()
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.\-_]*", name):
                loads.add(name + ".sty")
    classes = {m.group(1).strip() + ".cls" for m in _CLASS.finditer(t) if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.\-_]*", m.group(1).strip())}
    inputs = {m.group(1) for m in _INPUT.finditer(t)}
    for m in re.finditer(r"\\usetikzlibrary\s*\{([^}]+)\}", t):
        for name in m.group(1).split(","):
            name = name.strip()
            if re.fullmatch(r"[A-Za-z0-9.\-]+", name):
                inputs.update({f"tikzlibrary{name}.code.tex", f"pgflibrary{name}.code.tex"})
    return loads | classes, inputs


def environments_from_commands(cmds: set[str]) -> set[str]:
    """An environment is \\name ... \\endname: LaTeX's own and those defined with \\def come out this way."""
    return {c for c in cmds if "end" + c in cmds}


def _project_texts(files: dict[str, str]) -> str:
    return "\n".join(t for rel, t in files.items() if rel.endswith((".tex", ".sty", ".cls")))


def mine(files: dict[str, str]) -> list[dict]:
    """The commands the project defines itself, with how many arguments, for the suggestions."""
    out: dict[str, dict] = {}
    for rel, text in files.items():
        if not rel.endswith((".tex", ".sty", ".cls")):
            continue
        t = _COMMENT.sub("", text)
        for m in re.finditer(r"\\(?:re)?newcommand\*?\s*\{?\s*\\([A-Za-z]+)\s*\}?\s*(?:\[(\d)\])?|\\providecommand\*?\s*\{?\s*\\([A-Za-z]+)\s*\}?\s*(?:\[(\d)\])?"
                             r"|\\DeclareMathOperator\*?\s*\{?\s*\\([A-Za-z]+)|\\DeclarePairedDelimiter\s*\{?\s*\\([A-Za-z]+)|\\def\s*\\([A-Za-z]+)(#\d)*", t):
            name = m.group(1) or m.group(3) or m.group(5) or m.group(6) or m.group(7)
            args = int(m.group(2) or m.group(4) or 0)
            if m.group(6):
                args = 1
            if name and name not in out:
                out[name] = {"name": name, "args": args, "what": f"defined in {rel}"}
    return sorted(out.values(), key=lambda x: x["name"].lower())


def known(files: dict[str, str], learned: list[str] | None = None) -> dict:
    """Everything LaTeX knows in a project whose text files are `files` (path -> text)."""
    proj_cmds: set[str] = set()
    proj_envs: set[str] = set()
    loads, inputs = scan_text(_project_texts(files), proj_cmds, proj_envs)
    own = {Path(rel).name for rel in files}
    wanted = sorted((loads | {"article.cls"}) - own)               # the project's own .sty files were read above
    key = (tuple(wanted), bool(_kpsewhich()))
    with _guard:
        hit = _cache.get(key)
    if not hit or time.time() - hit["at"] > 3600:
        hit = _scan_installed(wanted)
        hit["at"] = time.time()
        with _guard:
            _cache[key] = hit
    cmds = set(hit["cmds"]) | proj_cmds | PRIMITIVES | EXTRA | set(learned or [])
    envs = set(hit["envs"]) | proj_envs | environments_from_commands(cmds)
    return {"known": sorted(cmds), "envs": sorted(envs), "mine": mine(files), "complete": hit["complete"],
            "missing": hit["missing"], "read": hit["read"], "clashes": clashes(files, hit) if hit["read"] else []}


_NEWCMD = re.compile(r"\\(?:newcommand|DeclareMathOperator)\*?\s*\{?\s*\\([A-Za-z]+)")
_NEWENV = re.compile(r"\\(?:newtheorem|newenvironment)\*?\s*\{([A-Za-z]+)\}")


def clashes(files: dict[str, str], hit: dict) -> list[dict]:
    """Names the project defines with \\newcommand, \\newtheorem or \\newenvironment that LaTeX or a loaded package
    already has: LaTeX stops there with "Command ... already defined". A definition guarded on its line
    (\\ifcsname, \\@ifundefined) is not one."""
    have = set(hit["cmds"]) | PRIMITIVES | EXTRA
    have_env = set(hit["envs"]) | environments_from_commands(have)
    out: dict[str, dict] = {}
    for rel, text in files.items():
        if not rel.endswith((".tex", ".sty", ".cls")):
            continue
        for line in _COMMENT.sub("", text).splitlines():
            if "\\ifcsname" in line or "@ifundefined" in line or "\\ifdefined" in line:
                continue
            for m in _NEWCMD.finditer(line):
                if m.group(1) in have:
                    out[m.group(1)] = {"name": m.group(1), "kind": "command"}
            for m in _NEWENV.finditer(line):
                if m.group(1) in have_env or m.group(1) in have:
                    out[m.group(1)] = {"name": m.group(1), "kind": "environment"}
    return sorted(out.values(), key=lambda x: x["name"])


def _scan_installed(wanted: list[str]) -> dict:
    cmds: set[str] = set()
    envs: set[str] = set()
    if not _kpsewhich():
        return {"cmds": [], "envs": [], "complete": False, "missing": [], "read": 0}
    seen: set[str] = set()
    todo = list(dict.fromkeys(KERNEL + wanted))
    missing: list[str] = []
    total = 0
    while todo and len(seen) < MAX_FILES and total < MAX_BYTES:
        batch = [n for n in todo if n not in seen][:120]
        todo = [n for n in todo if n not in seen and n not in batch]
        if not batch:
            break
        seen.update(batch)
        where = _find(batch)
        for name in batch:
            path = where.get(name)
            if not path:
                if name.endswith((".sty", ".cls")) and name in wanted:
                    missing.append(name[:-4])
                continue
            try:
                data = path.read_bytes()
            except OSError:
                continue
            total += len(data)
            more, inputs = scan_text(data.decode("latin-1"), cmds, envs)
            todo.extend(n for n in sorted(more | inputs) if n not in seen)
    return {"cmds": sorted(cmds), "envs": sorted(envs), "complete": not missing and total < MAX_BYTES and len(seen) < MAX_FILES,
            "missing": sorted(set(missing)), "read": len(seen)}


_USED = re.compile(r"\\([A-Za-z]+)")


def used_commands(files: dict[str, str]) -> set[str]:
    return {m.group(1) for rel, t in files.items() if rel.endswith(".tex") for m in _USED.finditer(_COMMENT.sub("", t))}


def undefined_in_log(errors: list[dict]) -> set[str]:
    """The commands a compile said it did not know ("Undefined control sequence"), read from the error's context."""
    out: set[str] = set()
    for e in errors:
        if "Undefined control sequence" not in (e.get("message") or ""):
            continue
        ctx = e.get("context") or ""
        first = ctx.split("\n", 1)[0]
        m = re.search(r"(\\[A-Za-z@]+)\s*$", first)
        if m:
            out.add(m.group(1)[1:])
    return out
