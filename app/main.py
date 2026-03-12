"""Wrapper to expose the FastAPI app for deployment.

The actual app lives at the repo root (main.py) and imports modules
from this ``app`` package.  This thin re-export lets deployment tools
that expect ``app.main:app`` find the instance without duplicating code.
"""

import sys
from pathlib import Path

# Ensure the repo root is on sys.path so ``import main`` resolves
_repo_root = str(Path(__file__).resolve().parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from main import app  # noqa: E402, F401 — re-export the FastAPI instance
