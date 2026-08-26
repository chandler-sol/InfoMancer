from __future__ import annotations

import os
import shutil
from pathlib import Path


root = Path(os.environ.get("INFOMANCER_E2E_ROOT", ".e2e-runtime")).resolve()
if root.exists():
    shutil.rmtree(root)

movies = root / "media" / "Movies"
for bucket in ("A", "B"):
    (movies / bucket).mkdir(parents=True, exist_ok=True)

names = [
    "Acceptance One (2001).mkv",
    "Acceptance Two (2002).mkv",
    "Acceptance Three (2003).mkv",
    "Acceptance Four (2004).mkv",
    "Acceptance Five (2005).mkv",
    "Acceptance Six (2006).mkv",
    "Acceptance Seven (2007).mkv",
    "Acceptance Eight (2008).mkv",
    "Acceptance Nine (2009).mkv",
    "Acceptance Ten (2010).mkv",
    "Acceptance Eleven (2011).mkv",
    "Acceptance Twelve (2012).mkv",
]

for index, name in enumerate(names):
    bucket = "A" if index < 6 else "B"
    path = movies / bucket / name
    path.write_bytes(b"InfoMancer acceptance fixture\n")

(root / "state").mkdir(parents=True, exist_ok=True)
(root / "logs").mkdir(parents=True, exist_ok=True)
print(root)
