"""Question-owned entry point; the first-attempt runner belongs to this study."""
from pathlib import Path
import sys
sys.path.insert(0, sys.argv[sys.argv.index("--field-kit-runtime") + 1])
from fieldkit_runtime.experiments.qwen.splash_study import main
raise SystemExit(main(Path(__file__).resolve().parent))
