# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vaibhav Chhajer
with open('C:/Users/vaibh/Desktop/scriptavera/scriptavera/server.py', 'r', encoding='utf-8') as f:
    text = f.read()

import re
for m in re.finditer(r'@app\.(get|post)\(["\']([^"\']+)["\']', text):
    print(m.group(1).upper(), m.group(2))
