"""Run two serial unchanged Brush controls; pass --output to retain independent jobs."""
import importlib.util
import json
from pathlib import Path

plan=json.loads(Path('docs/benchmarks/track-observations-plan.json').read_text())
spec=importlib.util.spec_from_file_location('controls',Path('docs/benchmarks/repeatability-run.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
module.SOURCES=plan['sources'];module.ORDER=plan['new_order'];module.CONFIG=plan['config']
if __name__=='__main__':module.main()
