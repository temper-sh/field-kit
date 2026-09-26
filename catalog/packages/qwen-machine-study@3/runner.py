from pathlib import Path
import sys
sys.path.insert(0, sys.argv[sys.argv.index("--field-kit-runtime") + 1])
from fieldkit_runtime.experiments.qwen.protocol import main
raise SystemExit(main(Path(__file__).resolve().parent))
