"""V3 카카오 매니저 작업대 진입점 (PyInstaller 빌드용)."""
from __future__ import annotations

import sys
from pathlib import Path

SPIKE_DIR = Path(__file__).resolve().parent / "tools" / "v3_spike"
if str(SPIKE_DIR) not in sys.path:
    sys.path.insert(0, str(SPIKE_DIR))

from pyqt_workspace_prototype import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
