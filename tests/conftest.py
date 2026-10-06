import os
import sys
import tempfile
from pathlib import Path

# Isolated runtime + offline components for every test session (no model download, no Ollama).
_tmp = tempfile.mkdtemp(prefix="uniassist-test-")
os.environ.update({"RUNTIME_DIR": _tmp, "EMBED_MODEL": "hash", "LLM_PROVIDER": "mock", "TAU": "0.25", "TAU_CONFIDENT": "0.5", "MIN_COVERAGE": "0.5"})
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
