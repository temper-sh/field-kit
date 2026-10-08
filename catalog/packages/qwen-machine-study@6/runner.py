"""Run only the configuration bound into this revision 6 package."""
from pathlib import Path
import sys
sys.path.insert(0, sys.argv[sys.argv.index("--field-kit-runtime") + 1])
from fieldkit_runtime.experiments.qwen.chunks import ChunkStudy
from fieldkit_runtime.experiments.qwen.splash_study import main
raise SystemExit(main(Path(__file__).resolve().parent, study_type=ChunkStudy))
