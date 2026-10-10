# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""The target journal: finding it, and the requirements the final paper has to meet.

Nothing here is a fixed list of journals. A journal is looked up by name in OpenAlex or Crossref, which between
them index practically every scholarly journal, and its publisher decides which LaTeX template family applies.
The requirements themselves (abstract length, keywords, classification, reference style, statements) come from
three layers, the later overriding the earlier:

  1. what is typical for the publisher (clearly marked as typical, never asserted about one journal);
  2. what the journal's own guide for authors says, read at the author's request and shown for confirmation;
  3. what the author sets by hand.
"""

from __future__ import annotations

import html as htmllib
import re
from urllib.parse import urljoin, urlparse

import requests

from . import __version__, config
from .util import loads_lenient

# LaTeX templates the app can produce itself. `tested` says whether this build was compiled with that class.
TEMPLATES: dict[str, dict] = {
    "amsart": {"label": "AMS article (amsart)", "tested": True,
               "note": "The class of the American Mathematical Society; the customary layout for pure mathematics."},
    "article": {"label": "Standard article", "tested": True,
                "note": "The standard LaTeX article class, accepted by most journals for a first submission."},
    "elsarticle": {"label": "Elsevier (elsarticle)", "tested": False,
                   "note": "Elsevier's class. It could not be compiled in the environment this app was built in; "
                           "if it fails on this computer the PDF falls back to the standard article class."},
}

DEFAULT_PROFILE: dict = {
    "template": "amsart",
    "abstract_min_words": None, "abstract_max_words": None,
    "keywords_min": None, "keywords_max": None,
    "msc": "optional",                 # required | optional | none
    "ref_style": "numeric",            # numeric | alpha
    "ref_order": "alphabetical",       # alphabetical | appearance
    "anonymous": False, "line_numbers": False, "double_spacing": False,
    "declarations": False,             # statements on funding, competing interests, data availability
    "page_limit": None,
    "notes": [],
}
PROFILE_KEYS = tuple(DEFAULT_PROFILE)

# Publisher families. `match` is tested against the publisher's name; the first hit wins.
# `house` names the publisher's own class only where its name is certain; `typical` holds what is usual for the
# publisher's mathematics journals and is always shown as "typical", because single journals differ.
FAMILIES: list[dict] = [
    {"id": "ams", "name": "American Mathematical Society", "match": ["american mathematical society"],
     "template": "amsart", "house": "amsart",
     "typical": {"msc": "required", "ref_order": "alphabetical"}},
    {"id": "elsevier", "name": "Elsevier", "match": ["elsevier", "academic press", "north-holland", "pergamon"],
     "template": "elsarticle", "house": "elsarticle",
     "typical": {"ref_order": "appearance"}},
    {"id": "springer", "name": "Springer Nature",
     "match": ["springer", "birkhäuser", "birkhauser", "biomed central", "nature portfolio"],
     "template": "article", "house": "sn-jnl (the Springer Nature LaTeX template)",
     "typical": {"abstract_min_words": 150, "abstract_max_words": 250, "keywords_min": 4, "keywords_max": 6,
                 "declarations": True, "ref_order": "appearance"}},
    {"id": "tandf", "name": "Taylor & Francis", "match": ["taylor & francis", "taylor and francis", "informa", "routledge"],
     "template": "article", "house": "interact (the Taylor & Francis template)", "typical": {}},
    {"id": "siam", "name": "SIAM", "match": ["society for industrial and applied mathematics",
                                               "society for industrial & applied mathematics"],
     "template": "article", "house": "siamart",
     "typical": {"abstract_max_words": 250, "msc": "required", "ref_order": "alphabetical"}},
    {"id": "wiley", "name": "Wiley", "match": ["wiley", "london mathematical society"],
     "template": "article", "house": "", "typical": {}},
    {"id": "cup", "name": "Cambridge University Press", "match": ["cambridge university press"],
     "template": "article", "house": "", "typical": {}},
    {"id": "oup", "name": "Oxford University Press", "match": ["oxford university press"],
     "template": "article", "house": "", "typical": {}},
    {"id": "degruyter", "name": "De Gruyter", "match": ["de gruyter", "degruyter"],
     "template": "article", "house": "", "typical": {}},
    {"id": "ems", "name": "EMS Press", "match": ["european mathematical society", "ems press"],
     "template": "amsart", "house": "", "typical": {"msc": "required"}},
    {"id": "worldsci", "name": "World Scientific", "match": ["world scientific"],
     "template": "article", "house": "", "typical": {}},
    {"id": "msp", "name": "Mathematical Sciences Publishers", "match": ["mathematical sciences publishers"],
     "template": "amsart", "house": "", "typical": {"msc": "required"}},
    {"id": "duke", "name": "Duke University Press", "match": ["duke university press"],
     "template": "amsart", "house": "", "typical": {"msc": "required"}},
    {"id": "ims", "name": "Institute of Mathematical Statistics", "match": ["institute of mathematical statistics"],
     "template": "article", "house": "", "typical": {}},
    {"id": "mdpi", "name": "MDPI", "match": ["mdpi", "multidisciplinary digital publishing"],
     "template": "article", "house": "", "typical": {}},
    {"id": "iop", "name": "IOP Publishing", "match": ["iop publishing", "institute of physics"],
     "template": "article", "house": "", "typical": {}},
    {"id": "generic", "name": "", "match": [], "template": "amsart", "house": "", "typical": {}},
]
_FAMILY = {f["id"]: f for f in FAMILIES}


def family_for(publisher: str) -> dict:
    """The publisher family, from the publisher's name. Whole words only: "Informa" is Taylor & Francis,
    "Information Press" is not."""
    low = (publisher or "").lower()
    for f in FAMILIES:
        if any(re.search(r"(?<![a-z])" + re.escape(m) + r"(?![a-z])", low) for m in f["match"]):
            return f
    return _FAMILY["generic"]


def family(fid: str) -> dict:
    return _FAMILY.get(fid) or _FAMILY["generic"]


# ------------------------------------------------------------------ lookup

def _session() -> requests.Session:
    s = requests.Session()
    email = config.get_env("CONTACT_EMAIL")
    s.headers["User-Agent"] = f"Scriptavera/{__version__} (research writing tool" + (f"; mailto:{email}" if email else "") + ")"
    return s


def _journal(name: str, publisher: str = "", issn: str = "", homepage: str = "", **extra) -> dict:
    fam = family_for(publisher)
    return {"name": re.sub(r"\s+", " ", name or "").strip(), "publisher": (publisher or "").strip(), "issn": issn or "",
            "homepage": homepage or "", "family": fam["id"], **extra}


def _openalex(query: str, limit: int) -> list[dict]:
    params = {"search": query, "filter": "type:journal", "per-page": limit,
              "select": "id,display_name,issn_l,issn,host_organization_name,homepage_url,works_count,summary_stats,country_code"}
    if config.get_env("OPENALEX_API_KEY"):
        params["api_key"] = config.get_env("OPENALEX_API_KEY")
    if config.get_env("CONTACT_EMAIL"):
        params["mailto"] = config.get_env("CONTACT_EMAIL")
    r = _session().get("https://api.openalex.org/sources", params=params, timeout=25)
    if r.status_code != 200:
        raise RuntimeError(f"OpenAlex HTTP {r.status_code}")
    out = []
    for it in (r.json() or {}).get("results") or []:
        if not isinstance(it, dict) or not it.get("display_name"):
            continue
        stats = it.get("summary_stats") or {}
        issns = it.get("issn") or []
        out.append(_journal(it["display_name"], it.get("host_organization_name") or "",
                            it.get("issn_l") or (issns[0] if issns else ""), it.get("homepage_url") or "",
                            works=it.get("works_count") or 0, h_index=stats.get("h_index"),
                            country=it.get("country_code") or "", source="OpenAlex"))
    return out


def _crossref(query: str, limit: int) -> list[dict]:
    params = {"query": query, "rows": limit}
    if config.get_env("CONTACT_EMAIL"):
        params["mailto"] = config.get_env("CONTACT_EMAIL")
    r = _session().get("https://api.crossref.org/journals", params=params, timeout=25)
    if r.status_code != 200:
        raise RuntimeError(f"Crossref HTTP {r.status_code}")
    out = []
    for it in ((r.json() or {}).get("message") or {}).get("items") or []:
        if not isinstance(it, dict) or not it.get("title"):
            continue
        issns = it.get("ISSN") or []
        counts = it.get("counts") or {}
        out.append(_journal(it["title"], it.get("publisher") or "", issns[0] if issns else "", "",
                            works=counts.get("total-dois") or 0, h_index=None, country="", source="Crossref"))
    return out


def search(query: str, limit: int = 12) -> tuple[list[dict], str]:
    """Journals whose name matches. Returns (journals, message). The message is empty unless something failed."""
    query = re.sub(r"\s+", " ", query or "").strip()
    if len(query) < 3:
        return [], ""
    if config.mock_mode():
        return config.simulator().journals(query), ""
    problems = []
    for name, fn in (("OpenAlex", _openalex), ("Crossref", _crossref)):
        try:
            found = fn(query, limit)
        except Exception as e:  # noqa: BLE001
            problems.append(f"{name}: {str(e)[:80]}")
            continue
        if found:
            seen: set[str] = set()
            unique = []
            for j in found:
                k = j["issn"] or j["name"].lower()
                if k not in seen:
                    seen.add(k)
                    unique.append(j)
            return unique, ""
    if problems:
        return [], "The journal databases could not be reached (" + "; ".join(problems) + "). You can type the journal's name and publisher by hand."
    return [], ""


# ----------------------------------------------------------------- profile

def _clean(values: dict | None) -> dict:
    """Keep only known requirement fields with sane values."""
    out: dict = {}
    for k, v in (values or {}).items():
        if k not in PROFILE_KEYS or v is None:
            continue
        if k in ("abstract_min_words", "abstract_max_words", "keywords_min", "keywords_max", "page_limit"):
            try:
                n = int(v)
            except (TypeError, ValueError):
                continue
            lo, hi = {"abstract_min_words": (20, 600), "abstract_max_words": (30, 1000), "keywords_min": (1, 15),
                      "keywords_max": (1, 20), "page_limit": (1, 500)}[k]
            if lo <= n <= hi:
                out[k] = n
        elif k == "msc":
            if str(v).lower() in ("required", "optional", "none"):
                out[k] = str(v).lower()
        elif k == "ref_style":
            if str(v).lower() in ("numeric", "alpha"):
                out[k] = str(v).lower()
        elif k == "ref_order":
            if str(v).lower() in ("alphabetical", "appearance"):
                out[k] = str(v).lower()
        elif k == "template":
            if v in TEMPLATES:
                out[k] = v
        elif k in ("anonymous", "line_numbers", "double_spacing", "declarations"):
            if isinstance(v, bool):
                out[k] = v
        elif k == "notes":
            if isinstance(v, list):
                out[k] = [re.sub(r"\s+", " ", str(x)).strip()[:260] for x in v if str(x).strip()][:8]
    if out.get("abstract_min_words") and out.get("abstract_max_words") and out["abstract_min_words"] > out["abstract_max_words"]:
        out.pop("abstract_min_words")
    if out.get("keywords_min") and out.get("keywords_max") and out["keywords_min"] > out["keywords_max"]:
        out.pop("keywords_min")
    return out


def _clean_guide(values: dict | None) -> dict | None:
    """What was read from a journal's guide and accepted by the author: requirement values plus the class it names."""
    if not isinstance(values, dict):
        return None
    out = _clean(values)
    cls = re.sub(r"[^A-Za-z0-9\-]", "", str(values.get("latex_class") or ""))[:40]
    if cls:
        out["latex_class"] = cls
    return out or None


def clean_journal(journal: dict | None) -> dict | None:
    """A target journal as it is stored with a project. None when no journal is named."""
    if not isinstance(journal, dict) or not str(journal.get("name") or "").strip():
        return None
    fam = family(journal["family"]) if journal.get("family") in _FAMILY else family_for(str(journal.get("publisher") or ""))
    one = lambda v, n: re.sub(r"\s+", " ", str(v or "")).strip()[:n]                 # noqa: E731
    home = one(journal.get("homepage"), 400)
    guide_url = one(journal.get("guide_url"), 400)
    return {"name": one(journal.get("name"), 200), "publisher": one(journal.get("publisher"), 200),
            "issn": one(journal.get("issn"), 20), "homepage": home if urlparse(home).scheme in ("http", "https") else "",
            "family": fam["id"], "guide": _clean_guide(journal.get("guide")),
            "guide_url": guide_url if urlparse(guide_url).scheme in ("http", "https") else "",
            "custom": _clean(journal.get("custom"))}


def profile(journal: dict | None) -> dict:
    """The requirements in force for a paper, with the origin of each value."""
    settings = config.load_settings()
    prof = dict(DEFAULT_PROFILE)
    origin = {k: "default" for k in prof}
    if settings.get("document_class") in TEMPLATES:
        prof["template"] = settings["document_class"]
    if settings.get("ref_order") in ("alphabetical", "appearance"):
        prof["ref_order"] = settings["ref_order"]
    if not journal:
        return {**prof, "origin": origin, "journal": None, "family": "generic", "house": ""}
    fam = family(journal.get("family") or family_for(journal.get("publisher", ""))["id"])
    layers = [("typical", {"template": fam["template"], **fam["typical"]}),
              ("guide", _clean(journal.get("guide"))),
              ("author", _clean(journal.get("custom")))]
    for name, values in layers:
        for k, v in _clean(values).items():
            prof[k] = v
            origin[k] = name
    guide = journal.get("guide") or {}
    cls = str(guide.get("latex_class") or "").lower()
    if origin["template"] != "author":
        for t in TEMPLATES:                               # the guide names a class the app can produce
            if t != "article" and t in cls:
                prof["template"], origin["template"] = t, "guide"
    return {**prof, "origin": origin, "journal": journal.get("name") or None, "family": fam["id"],
            "house": fam["house"], "publisher": fam["name"] or journal.get("publisher", "")}


def words(text: str) -> int:
    """Words in an abstract the way journals count them: a formula counts as one word."""
    t = re.sub(r"\$[^$]*\$", " x ", text or "")
    t = re.sub(r"\\[A-Za-z]+\*?(\[[^\]]*\])?", " ", t)
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'’\-]*", t))


def length_phrase(prof: dict) -> str:
    lo, hi = prof.get("abstract_min_words"), prof.get("abstract_max_words")
    if lo and hi:
        return f"between {lo} and {hi} words"
    if hi:
        return f"at most {hi} words (aim for {max(40, int(hi * 0.8))} to {hi})"
    if lo:
        return f"at least {lo} words (aim for {lo} to {lo + 60})"
    return "90 to 170 words"


def keywords_phrase(prof: dict) -> str:
    lo, hi = prof.get("keywords_min"), prof.get("keywords_max")
    if lo and hi:
        return f"{lo} to {hi}" if lo != hi else str(lo)
    if hi:
        return f"{min(4, hi)} to {hi}" if hi > 4 else f"up to {hi}"
    if lo:
        return f"{lo} to {lo + 2}"
    return "4 to 6"


def checklist(paper: dict, prof: dict, statements: dict | None, pages: int | None = None) -> list[dict]:
    """Each requirement, whether the paper meets it, and what to do if not. Computed, never guessed."""
    out: list[dict] = []

    def add(label: str, ok: bool, detail: str, origin: str = "") -> None:
        out.append({"label": label, "ok": bool(ok), "detail": detail, "origin": origin})
    og = prof.get("origin", {})
    n = words(paper.get("abstract", ""))
    lo, hi = prof.get("abstract_min_words"), prof.get("abstract_max_words")
    if lo or hi:
        rng = f"{lo}–{hi}" if lo and hi else (f"at most {hi}" if hi else f"at least {lo}")
        add(f"Abstract of {rng} words", (not lo or n >= lo) and (not hi or n <= hi), f"The abstract has {n} words.",
            og.get("abstract_max_words") if hi else og.get("abstract_min_words"))
    k = len(paper.get("keywords") or [])
    lo, hi = prof.get("keywords_min"), prof.get("keywords_max")
    if lo or hi:
        rng = f"{lo}–{hi}" if lo and hi and lo != hi else (str(hi or lo) if lo == hi else (f"at most {hi}" if hi else f"at least {lo}"))
        add(f"Keywords: {rng}", (not lo or k >= lo) and (not hi or k <= hi), f"The paper has {k} keywords.",
            og.get("keywords_max") if hi else og.get("keywords_min"))
    if prof.get("msc") == "required":
        add("Mathematics Subject Classification", bool(paper.get("msc")),
            "Codes are present; check that they are the right ones." if paper.get("msc") else "No MSC codes yet. Add them in the paper view.",
            og.get("msc"))
    if prof.get("declarations"):
        st = statements or {}
        missing = [name for key, name in (("funding", "funding"), ("competing", "competing interests")) if not (st.get(key) or "").strip()]
        add("Statements on funding and competing interests", not missing,
            "Present." if not missing else "Missing: " + ", ".join(missing) + ". Only you can state these; fill them in under Journal.",
            og.get("declarations"))
    if prof.get("anonymous"):
        add("Anonymous manuscript", True, "Author names, affiliations, acknowledgements and funding are left out of this version.",
            og.get("anonymous"))
    if prof.get("page_limit") and pages:
        add(f"At most {prof['page_limit']} pages", pages <= prof["page_limit"], f"The PDF has {pages} pages.", og.get("page_limit"))
    return out


# ------------------------------------------------- the journal's own guide

_GUIDE_WORDS = re.compile(r"guide for authors|author guidelines|instructions? for authors|information for authors|"
                          r"submission guidelines|notes for contributors|for authors|author instructions|"
                          r"manuscript preparation|prepare your (paper|manuscript)", re.I)
_DROP_BLOCKS = re.compile(r"<(script|style|noscript|svg|nav|footer|header|form|iframe)\b.*?</\1\s*>", re.S | re.I)


def html_to_text(page: str) -> str:
    t = _DROP_BLOCKS.sub(" ", page or "")
    t = re.sub(r"<!--.*?-->", " ", t, flags=re.S)
    t = re.sub(r"<(br|/p|/div|/li|/h[1-6]|/tr|/section|/dd|/dt)\b[^>]*>", "\n", t, flags=re.I)
    t = re.sub(r"<li\b[^>]*>", "\n- ", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = htmllib.unescape(t)
    t = re.sub(r"[ \t\r\f\v\u00a0]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def guide_links(page: str, base: str) -> list[str]:
    """Links on a journal's home page that lead to its instructions for authors, best first."""
    page = re.sub(r"<(script|style|noscript)\b.*?</\1\s*>|<!--.*?-->", " ", page or "", flags=re.S | re.I)
    found: list[tuple[int, str]] = []
    for m in re.finditer(r"<a\b[^>]*?href\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", page, flags=re.S | re.I):
        href = htmllib.unescape(m.group(1)).strip().split("#", 1)[0]
        label = re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", m.group(2))).replace("\u00a0", " ")).strip()
        if not href or href.lower().startswith(("mailto:", "javascript:", "tel:")):
            continue
        score = (3 if _GUIDE_WORDS.search(label) else 0) + (2 if re.search(r"guide-for-authors|author-?guidelines|"
                                                                          r"instructions|submission-guidelines|for-authors", href, re.I) else 0)
        if score:
            found.append((score, urljoin(base, href)))
    out: list[str] = []
    for _, url in sorted(found, key=lambda x: -x[0]):
        if url not in out and urlparse(url).scheme in ("http", "https"):
            out.append(url)
    return out[:4]


def fetch(url: str) -> tuple[str, str]:
    """(raw html or '', plain text) of a page or PDF. Raises RuntimeError with a plain reason when it cannot be read."""
    if urlparse(url).scheme not in ("http", "https"):
        raise RuntimeError("That is not a web address.")
    try:
        r = _session().get(url, timeout=30, stream=True, allow_redirects=True,
                           headers={"Accept": "text/html,application/pdf;q=0.9,*/*;q=0.5"})
        if r.status_code in (401, 403, 429):
            raise RuntimeError("The publisher's site does not let programs read this page. Open it in your browser, "
                               "copy the text of the instructions and paste it here instead.")
        if r.status_code != 200:
            raise RuntimeError(f"The page answered with error {r.status_code}.")
        data = b""
        for chunk in r.iter_content(65536):
            data += chunk
            if len(data) > 6_000_000:
                break
    except requests.RequestException as e:
        raise RuntimeError(f"The page could not be reached ({type(e).__name__}).") from None
    if data[:5] == b"%PDF-":
        from .literature import _pdf_text
        try:
            return "", _pdf_text(data, max_pages=25)
        except Exception:  # noqa: BLE001
            raise RuntimeError("The PDF could not be read.") from None
    page = data.decode(r.encoding or "utf-8", "ignore")
    return page, html_to_text(page)


def looks_like_guide(text: str) -> bool:
    low = (text or "").lower()
    hits = sum(1 for w in ("abstract", "keywords", "references", "manuscript", "submission", "latex", "figures") if w in low)
    return hits >= 4 and len(low) > 1500


def read_guide(source: str, ask) -> dict:
    """Read a guide for authors given as a web address or as pasted text. `ask(system, user)` calls a model and
    returns its answer. Returns {"values": {...}, "url": str, "chars": int}. The values still need the author's eye."""
    from . import prompts
    source = (source or "").strip()
    url = ""
    if re.match(r"^https?://\S+$", source):
        url = source
        page, text = fetch(url)
        if not looks_like_guide(text) and page:
            for link in guide_links(page, url):               # a home page: follow its link to the instructions
                try:
                    page2, text2 = fetch(link)
                except RuntimeError:
                    continue
                if looks_like_guide(text2):
                    url, text = link, text2
                    break
    else:
        text = source
    if len(text) < 300:
        raise RuntimeError("There is too little text there to be a guide for authors. Paste the instructions themselves.")
    answer = ask(prompts.JOURNAL_GUIDE.format(), "TEXT OF THE PAGE\n\n" + text[:60000])
    try:
        data = loads_lenient(answer)
    except ValueError:
        raise RuntimeError("The instructions were read, but the result could not be understood. Try again.") from None
    if not isinstance(data, dict):
        raise RuntimeError("The instructions were read, but the result could not be understood. Try again.")
    values = _clean({k: data.get(k) for k in PROFILE_KEYS})
    style = str(data.get("ref_style") or "").lower()
    extra = []
    if "author" in style:
        extra.append("The journal uses author–year citations. This app writes numbered citations; journals convert them at production.")
    cls = re.sub(r"[^A-Za-z0-9\-]", "", str(data.get("latex_class") or ""))[:40]
    if cls:
        values["latex_class"] = cls
    if data.get("journal"):
        values["journal_named"] = re.sub(r"\s+", " ", str(data["journal"]))[:160]
    values["notes"] = (values.get("notes") or []) + extra
    return {"values": values, "url": url, "chars": len(text)}
