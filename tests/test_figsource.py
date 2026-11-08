# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Reading the pen strokes of a PDF page in little memory. A notes app stores each pen stroke as a filled outline, and
a long stroke's outline can have tens of thousands of points. Whether two outlines overlap was worked out with all the
points of one against all the edges of the other at once: two outlines of 20,000 and 14,000 points made tables of
2 GB, and drawing a figure on such a page failed with "MemoryError: Unable to allocate 2.14 GiB". The pages here are
made up."""
import math
import time
import tracemalloc
import numpy as np
from helpers import isolate
isolate("scriptavera_figsource_")
from scriptavera import figsource


def pdf_page(rings, w=600, h=860) -> bytes:
    """A PDF page with one path object: the outlines `rings`, filled together, as a notes app writes pen strokes."""
    path = []
    for ring in rings:
        path.append("%.2f %.2f m" % ring[0])
        path += ["%.2f %.2f l" % q for q in ring[1:]]
        path.append("h")
    content = ("0 g\n" + "\n".join(path) + "\nf\n").encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Contents 4 0 R >>" % (w, h),
            b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)


def stroke(n, a, b, wiggle=30.0):
    """The outline of a pen stroke from a to b that wiggles on the way, with n points."""
    half, (dx, dy) = n // 2, (b[0] - a[0], b[1] - a[1])
    L = math.hypot(dx, dy)
    nx, ny = -dy / L, dx / L
    left, right = [], []
    for k in range(half):
        t = k / (half - 1)
        x, y = a[0] + dx * t + wiggle * math.sin(40 * t), a[1] + dy * t + wiggle * math.cos(37 * t)
        left.append((x + nx, y + ny))
        right.append((x - nx, y - ny))
    return left + right[::-1]


def read(rings):
    src = figsource._open_pdf(pdf_page(rings), 0)
    tracemalloc.start()
    t0 = time.time()
    out = src.strokes()
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    return out, peak / 2 ** 20, time.time() - t0


# ---- 1. two long strokes that cross: one stroke, read in a few megabytes (it took over 2 GB)
got, mib, sec = read([stroke(20396, (40, 60), (560, 800)), stroke(14102, (560, 60), (40, 800))])
assert len(got) == 1 and len(got[0].rings) == 2, got
assert mib < 64, f"{mib:.0f} MiB"
print(f"1. two long crossing strokes (20,396 and 14,102 points) are one stroke, read in {mib:.0f} MiB and {sec:.1f} s")

# ---- 2. two long strokes apart stay two
got, mib, sec = read([stroke(9000, (40, 60), (300, 800), 5), stroke(9000, (320, 60), (580, 800), 5)])
assert len(got) == 2 and mib < 64, (len(got), mib)
print("2. two long strokes that do not touch stay two")

# ---- 3. the same answers as all points against all edges at once
def every(P, r):
    x, y = P[:, 0:1], P[:, 1:2]
    xs, ys = r[None, :, 0], r[None, :, 1]
    xs2, ys2 = np.roll(xs, -1, axis=1), np.roll(ys, -1, axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        xc = xs + (y - ys) * (xs2 - xs) / (ys2 - ys)
    return (np.count_nonzero(((ys > y) != (ys2 > y)) & (x < xc), axis=1) % 2).astype(bool)


rng = np.random.default_rng(7)
cells = figsource.RING_CELLS
figsource.RING_CELLS = 64                          # a few points at a time, to try the slices themselves
for it in range(600):
    m, n = int(rng.integers(3, 50)), int(rng.integers(1, 60))
    if it % 2:                                     # on a grid: points on edges and corners, flat edges
        r, P = rng.integers(0, 6, (m, 2)).astype(float), rng.integers(-1, 7, (n, 2)).astype(float)
    else:
        a = np.sort(rng.uniform(0, 2 * np.pi, m))
        r, P = np.c_[np.cos(a), np.sin(a)] * rng.uniform(0.3, 1, (m, 1)), rng.uniform(-1.2, 1.2, (n, 2))
    want = every(P, r)
    assert (figsource._points_in_ring(P, r) == want).all() and figsource._points_in_ring(P, r, True).any() == want.any()
figsource.RING_CELLS = cells
print("3. which points lie inside an outline: the same answers as before, a little at a time")
print("figsource ok")
