# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""SyncTeX: from a line of the source to its place in the PDF, and back.

LaTeX writes main.synctex.gz next to the PDF when it is run with -synctex=1. The file records, for every box TeX
put on a page, which input file and which line produced it, and where it is. Positions are in scaled points from
the top-left corner of the page; here they are converted to PDF points (1/72 inch), the unit the viewer uses.

Reading the file in Python (instead of calling the synctex program) works the same with TeX Live and MiKTeX and
needs nothing installed beyond LaTeX itself.
"""

from __future__ import annotations

import gzip
import os
import re
from pathlib import Path

SP_PER_BP = 65781.76             # scaled points in one PDF point (65536 sp = 1 TeX pt; 72.27 pt = 72 bp)
_REC = re.compile(r"^([\[(hvxkg$])(\d+),(\d+):(-?\d+),(-?\d+)(?::(-?\d+)(?:,(-?\d+),(-?\d+))?)?")


class Box:
    __slots__ = ("kind", "tag", "line", "h", "v", "w", "ht", "dp")

    def __init__(self, kind: str, tag: int, line: int, h: float, v: float, w: float, ht: float, dp: float):
        self.kind, self.tag, self.line, self.h, self.v, self.w, self.ht, self.dp = kind, tag, line, h, v, w, ht, dp

    def rect(self) -> tuple[float, float, float, float]:
        """(left, top, right, bottom) in PDF points from the top-left corner of the page."""
        return self.h, self.v - self.ht, self.h + max(self.w, 0.0), self.v + self.dp


class SyncTeX:
    """One parsed main.synctex.gz."""

    def __init__(self, path: Path | str, root: Path | str):
        self.root = Path(root)
        self.inputs: dict[int, str] = {}          # tag -> path as LaTeX opened it
        self.pages: dict[int, list[Box]] = {}
        self._parse(Path(path))

    # ------------------------------------------------------------ reading

    def _parse(self, path: Path) -> None:
        opener = gzip.open if path.suffix == ".gz" else open
        unit, mag, xoff, yoff = 1.0, 1000.0, 0.0, 0.0
        page = 0
        boxes: list[Box] = []
        with opener(path, "rt", encoding="utf-8", errors="replace") as f:
            content = False
            for raw in f:
                line = raw.rstrip("\n")
                if not line:
                    continue
                if line.startswith("Input:"):
                    m = re.match(r"^Input:(\d+):(.*)$", line)
                    if m:
                        self.inputs[int(m.group(1))] = m.group(2)
                    continue
                if not content:
                    if line.startswith("Unit:"):
                        unit = float(line[5:] or 1)
                    elif line.startswith("Magnification:"):
                        mag = float(line[14:] or 1000)
                    elif line.startswith("X Offset:"):
                        xoff = float(line[9:] or 0)
                    elif line.startswith("Y Offset:"):
                        yoff = float(line[9:] or 0)
                    elif line.startswith("Content:"):
                        content = True
                    continue
                c = line[0]
                if c == "{":
                    page = int(re.match(r"\{(\d+)", line).group(1)) if re.match(r"\{(\d+)", line) else page + 1
                    boxes = self.pages.setdefault(page, [])
                    continue
                if c in "}!" or line.startswith("Postamble"):
                    continue
                m = _REC.match(line)
                if not m or not page:
                    continue
                scale = unit * mag / 1000.0 / SP_PER_BP
                kind, tag, ln = m.group(1), int(m.group(2)), int(m.group(3))
                h = int(m.group(4)) * scale + xoff / SP_PER_BP
                v = int(m.group(5)) * scale + yoff / SP_PER_BP
                w = int(m.group(6)) * scale if m.group(6) else 0.0
                ht = int(m.group(7)) * scale if m.group(7) else 0.0
                dp = int(m.group(8)) * scale if m.group(8) else 0.0
                if kind == "k":                     # a kern: its width is the third number; it has no height
                    ht = dp = 0.0
                boxes.append(Box(kind, tag, ln, h, v, w, ht, dp))

    # ------------------------------------------------------------ files

    def relative(self, tag: int) -> str | None:
        """The input file of a tag as a path inside the project, or None for files outside it (classes, packages)."""
        raw = self.inputs.get(tag)
        if not raw:
            return None
        return to_relative(raw, self.root)

    def tags_for(self, rel: str) -> list[int]:
        want = rel.replace("\\", "/").lstrip("./")
        if not want.endswith(".tex") and "." not in want.rsplit("/", 1)[-1]:
            want += ".tex"
        return [t for t in self.inputs if (self.relative(t) or "") == want]

    # ------------------------------------------------------------ searches

    def forward(self, rel: str, line: int) -> dict | None:
        """Where a line of a source file is typeset: {page, x, y, w, h} in PDF points from the page's top-left."""
        tags = set(self.tags_for(rel))
        if not tags:
            return None
        by_line: dict[int, dict[int, list[Box]]] = {}     # line -> page -> records
        for page, boxes in self.pages.items():
            for b in boxes:
                if b.tag in tags:
                    by_line.setdefault(b.line, {}).setdefault(page, []).append(b)
        if not by_line:
            return None

        def solid(recs: list[Box]) -> list[Box]:
            return [b for b in recs if b.kind in "(h" and b.w > 0]
        # the line itself, then the next few lines (a blank line or a \begin), then earlier ones; text boxes first
        order = [line] + [line + k for k in range(1, 6)] + [line - k for k in range(1, 40)]
        chosen = None
        for want_solid in (True, False):
            for ln in order:
                pages = by_line.get(ln)
                if not pages:
                    continue
                usable = {pg: (solid(rs) if want_solid else rs) for pg, rs in pages.items()}
                usable = {pg: rs for pg, rs in usable.items() if rs}
                if usable:
                    page = min(usable)
                    chosen = (page, usable[page])
                    break
            if chosen:
                break
        if not chosen:
            ln = min(by_line, key=lambda k: abs(k - line))
            page = min(by_line[ln])
            chosen = (page, by_line[ln][page])
        page, found = chosen
        first = min(found, key=lambda b: b.v)           # the first line of the passage, and what is beside it
        found = [b for b in found if abs(b.v - first.v) <= 14]
        hits = {page: found}
        left = min(b.rect()[0] for b in found)
        top = min(b.rect()[1] for b in found)
        right = max(b.rect()[2] for b in found)
        bottom = max(b.rect()[3] for b in found)
        if bottom - top < 4:                         # a point record (glue, kern): show a line's height around it
            top, bottom = top - 9, bottom + 3
        if right - left < 4:
            right = left + 40
        return {"page": page, "x": round(left, 2), "y": round(top, 2), "w": round(right - left, 2),
                "h": round(bottom - top, 2), "line": hits[page][0].line}

    def inverse(self, page: int, x: float, y: float) -> dict | None:
        """The source line that produced what is at (x, y) on a page, in PDF points from the page's top-left."""
        boxes = [b for b in self.pages.get(page, []) if self.relative(b.tag)]
        if not boxes:
            return None
        inside = []
        for b in boxes:
            if b.kind not in "(h" or b.w <= 0:
                continue
            l, t, r, btm = b.rect()
            if l - 1 <= x <= r + 1 and t - 2 <= y <= btm + 2:
                inside.append(((r - l) * max(btm - t, 1.0), b))
        if inside:
            inside.sort(key=lambda z: z[0])
            # the smallest line box around the point; among the records inside it, the one nearest the point
            box = inside[0][1]
            l, t, r, btm = box.rect()
            near = [b for b in boxes if b.kind in "xkg$" and l - 1 <= b.h <= r + 1 and t - 2 <= b.v <= btm + 2]
            pick = min(near, key=lambda b: abs(b.h - x)) if near else box
        else:
            pick = min(boxes, key=lambda b: abs(b.v - y) * 3 + abs(b.h - x) * 0.3)
        return {"path": self.relative(pick.tag), "line": pick.line}


def to_relative(raw: str, root: Path) -> str | None:
    """A path as LaTeX recorded it (absolute, or relative to the folder LaTeX ran in) as a path inside `root`."""
    s = raw.strip().replace("\\", "/")
    while "/./" in s:
        s = s.replace("/./", "/")
    if s.startswith("./"):
        s = s[2:]
    base = os.path.normpath(str(root)).replace("\\", "/").rstrip("/")
    norm = os.path.normpath(s).replace("\\", "/") if s else s
    if re.match(r"^(?:/|[A-Za-z]:/)", norm):
        a, b = norm, base + "/"
        if os.name == "nt" or re.match(r"^[A-Za-z]:/", norm):
            a, b = a.lower(), b.lower()
        if not a.startswith(b):
            return None
        rel = norm[len(base) + 1:]
    else:
        rel = norm
    if rel.startswith("../") or not rel:
        return None
    return rel


def load(build_dir: Path, root: Path, job: str = "main") -> SyncTeX | None:
    for name in (f"{job}.synctex.gz", f"{job}.synctex"):
        f = build_dir / name
        if f.exists():
            try:
                return SyncTeX(f, root)
            except (OSError, ValueError, EOFError):
                return None
    return None
