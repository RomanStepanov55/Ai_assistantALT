"""Start Alontito:  python alontito.py

Works from any directory: the project root is added to sys.path before the
package is imported, so the vision/bot modules resolve the same way they do
when the tests run.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.chdir(ROOT)                      # data/, debug_frames/ are relative to root

from alontito.app import main       # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
