"""Repository-level integration tests for the two independent deployments."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for deployment in ("collector", "website"):
    sys.path.insert(0, str(REPO_ROOT / deployment))
