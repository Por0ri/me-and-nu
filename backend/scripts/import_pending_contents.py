"""Local wrapper; the image uses python -m app.services.pending_content_import."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.pending_content_import import main


if __name__ == "__main__":
    raise SystemExit(main())
