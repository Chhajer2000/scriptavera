# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Shared by the tests: an isolated projects folder, synthetic page images, and a way to wait for the pipeline."""
import io, os, pathlib, sys, tempfile, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["SCRIPTAVERA_MOCK"] = "1"
os.environ.setdefault("SCRIPTAVERA_MOCK_DELAY", "0.0")


def isolate(prefix: str = "scriptavera_test_") -> pathlib.Path:
    """Point the app at a temporary folder so that no test touches real projects, settings or keys."""
    from scriptavera import config
    tmp = pathlib.Path(tempfile.mkdtemp(prefix=prefix))
    config.PROJECTS_DIR = tmp / "projects"
    config.PROJECTS_DIR.mkdir()
    config.SETTINGS_PATH = tmp / "settings.json"
    config.ENV_PATH = tmp / ".env"
    config.IDENTITY_DIR = tmp / "identity"
    config.MEMORY_DIR = tmp / "memory"
    config.PHONE_DIR = tmp / "phone_uploads"
    return tmp


def page_images(n: int = 6) -> list[tuple[str, bytes]]:
    """Pages with pen-like strokes (not blank), one of them carrying a drawing."""
    from PIL import Image, ImageDraw
    files = []
    for i in range(n):
        im = Image.new("RGB", (900, 1200), "white")
        d = ImageDraw.Draw(im)
        for y in range(90, 1100, 58):
            d.line((80, y, 780 - (y * 7) % 230, y + 4), fill=(25, 25, 60), width=3)
        if i == 0:
            d.rectangle((120, 330, 780, 600), outline=(25, 25, 60), width=3)
            d.arc((140, 200, 900, 760), 180, 262, fill=(25, 25, 60), width=3)
        b = io.BytesIO()
        im.save(b, "PNG")
        files.append((f"page{i + 1}.png", b.getvalue()))
    return files


def pages_pdf(n: int = 6) -> bytes:
    """The same pages as one PDF, for the upload dialog of the interface."""
    from PIL import Image
    imgs = [Image.open(io.BytesIO(data)).convert("RGB") for _, data in page_images(n)]
    b = io.BytesIO()
    imgs[0].save(b, "PDF", save_all=True, append_images=imgs[1:], resolution=110)
    return b.getvalue()


def new_project(meta: dict | None = None, pages: int = 6):
    from scriptavera import config, ingest, store
    pid = store.new_id()
    pgs = ingest.ingest(page_images(pages), config.PROJECTS_DIR / pid / "pages")
    base = {"name": "Test notes", "domains": ["math.CA"], "subarea": "inequalities for series", "hints": "", "title": "",
            "problem": "Explicit bounds for the partial sums of the series of reciprocal powers.",
            "keywords": ["partial sums", "reciprocal powers"], "key_papers": ["10.9999/example.2015.1"],
            "authors": [{"name": "Test Author", "affiliation": "Test University", "email": "t.author@example.org"}],
            "known_refs": [], "journal": None, "statements": {}}
    return store.create({**base, **(meta or {})}, pgs, pid)


def wait(p, target: str, statuses=("waiting", "done"), seconds: float = 180):
    """Wait until the stage has one of the statuses and nothing is running. A stopped run is a failure unless
    'error' or 'paused' is among the statuses asked for."""
    from scriptavera import pipeline
    t0 = time.time()
    while time.time() - t0 < seconds:
        st = p.state
        s = st["stages"][target].get("status")
        if s in statuses and not pipeline.is_running(p.id):
            return st["stages"][target]
        bad = next((x for x in st["stages"].values() if x.get("status") in ("error", "paused")), None)
        if bad and bad.get("status") not in statuses and not pipeline.is_running(p.id):
            trace = (p.dir / "last_error.txt").read_text() if (p.dir / "last_error.txt").exists() else ""
            raise SystemExit(f"STOPPED: {bad.get('error')}\n{trace}")
        time.sleep(0.05)
    raise SystemExit(f"timeout waiting for {target}: " + str({k: v.get('status') for k, v in p.state['stages'].items()}))


def past_topic(p):
    """The stop after the first search: confirm the topic as found."""
    from scriptavera import pipeline
    pipeline.start(p); wait(p, "topiccheck")
    pipeline.submit_topic(p, {"action": "confirm"})


def answers(p) -> dict:
    """The author's answers at the confirmation step: accept each proposal, dismiss nothing."""
    g = p.load("glossary")
    return {"glossary": [{"id": e["id"], "meaning": e["meaning"], "confirmed": True} for e in g["entries"]],
            "answers": [{"question": q["question"], "answer": "It ends with $\\log n$ in my notes."} for q in g["questions"]],
            "extra": [], "instructions": ""}
