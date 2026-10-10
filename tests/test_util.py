# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from scriptavera.util import *
from scriptavera.llm import classify

raw = r'''```json
{"a": "\frac{1}{2} and \nu \neq \tau, \alpha \le 3 \to \beta \rho \text{x} \underline{y}", "b": [1,2,],"c":"line1\nNext \"q\" é"}
```'''
d = loads_lenient(raw)
assert d["a"] == r"\frac{1}{2} and \nu \neq \tau, \alpha \le 3 \to \beta \rho \text{x} \underline{y}", d["a"]
assert d["b"] == [1, 2] and d["c"] == 'line1\nNext "q" é', d
assert to_float('0.8') == 0.8 and to_float('80%') == 0.8 and to_float('8/10') == 0.8
assert parse_records('<entry>\nsymbol: f(x)\nkind: symbol\nmeaning: number of x with x^2=1: includes e\n</entry>')[0]["meaning"].endswith("includes e")
assert parse_rows('- a || b || c\n2. d || e') == [['a', 'b', 'c'], ['d', 'e']]
assert extract_tag('<transcript>\nhello $x$\n</transcript>\n<gist>g</gist>', 'transcript') == 'hello $x$'
assert extract_tag('<transcript>\nabc\n<gist>\ng</gist>', 'transcript') == 'abc'
assert strip_think("<think>hmm</think>answer") == "answer"
for pid, mid, expect in [('gemini', 'models/gemini-3.8-flash', True), ('gemini', 'models/gemini-3.5-flash-lite', True),
                         ('gemini', 'gemini-3.8-flash-tts', False), ('gemini', 'gemini-3.1-pro-preview', True),
                         ('groq', 'qwen/qwen3.8-27b', True), ('groq', 'openai/gpt-oss-120b', True),
                         ('groq', 'whisper-large-v3', False), ('groq', 'openai/gpt-oss-safeguard-20b', False),
                         ('cerebras', 'qwen-3.8-27b', True), ('gemini', 'gemini-2.5-flash', True),
                         ('gemini', 'gemini-3.1-flash-image', False), ('gemini', 'gemini-3-flash-preview', True)]:
    m = classify(pid, mid)
    assert bool(m) == expect, (pid, mid, m)
print("util ok")
