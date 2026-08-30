from __future__ import annotations

import os
import shutil
from pathlib import Path


root = Path(os.environ.get("INFOMANCER_E2E_ROOT", ".e2e-runtime")).resolve()
if root.exists():
    shutil.rmtree(root)

movies = root / "media" / "Movies"
movies.mkdir(parents=True, exist_ok=True)
(movies / "CSP Acceptance (2026).mkv").write_bytes(b"InfoMancer 0.9 acceptance fixture\n")
(root / "state").mkdir(parents=True, exist_ok=True)
(root / "logs").mkdir(parents=True, exist_ok=True)
print(root)
