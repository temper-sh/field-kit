"""Use the Field Kit checkout this harness ships in, so format code and harness share one revision."""
from pathlib import Path
import sys

FIELD_KIT_ROOT = Path(__file__).resolve().parents[2]
if not (FIELD_KIT_ROOT / "fieldkit_runtime/adapters/models/__init__.py").is_file():
    raise RuntimeError("the coding harness must stay inside its Field Kit checkout")
sys.path.insert(0, str(FIELD_KIT_ROOT))
