# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
"""Run every test. Nothing here needs an API key or the internet: the models and the databases are simulated.

    python tests/run_all.py          the logic and the server
    python tests/run_all.py --ui     also drive the interface in a browser (needs the playwright package)
"""
import pathlib, subprocess, sys, time

HERE = pathlib.Path(__file__).resolve().parent
TESTS = ["lint_names", "test_util", "test_audit", "test_typography", "test_paper", "test_templates", "test_regressions", "test_literature",
         "test_journals", "test_router_http", "test_revision", "test_pipeline", "test_server", "test_migrate", "test_pages_figures", "test_terms", "test_leaks", "test_complete", "test_completion", "test_drawings", "test_keys_models", "test_many",
         "test_editor", "test_provenance", "test_latex_checks", "test_compute", "test_sources", "test_relevance", "test_reviews", "test_typed", "test_testmode", "test_costs", "test_phone", "test_refs_arxiv_memory"]
if "--ui" in sys.argv:
    TESTS += ["ui_walk", "ui_states", "ui_drawings"]
failed = []
for name in TESTS:
    t0 = time.time()
    r = subprocess.run([sys.executable, str(HERE / f"{name}.py")], capture_output=True, text=True, cwd=str(HERE), timeout=1200)
    last = (r.stdout.strip().splitlines() or [""])[-1]
    ok = r.returncode == 0
    print(f"{'ok  ' if ok else 'FAIL'} {name:<18} {time.time() - t0:5.1f}s  {last[:90]}")
    if not ok:
        failed.append(name)
        print("\n".join((r.stdout + r.stderr).strip().splitlines()[-12:]))
print("\nall tests passed" if not failed else f"\n{len(failed)} failed: {', '.join(failed)}")
sys.exit(1 if failed else 0)
