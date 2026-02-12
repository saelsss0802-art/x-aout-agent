from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE_SRC = ROOT / "packages" / "core"
for p in (ROOT, CORE_SRC):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
