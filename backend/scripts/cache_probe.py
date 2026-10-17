"""Single real GPU control run. Reference labels are never read."""
import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path
import time

from backend.analyzer.config import AnalyzerConfig
from backend.analyzer.errors import AnalyzerError
from backend.analyzer.pipeline import analyze_video_report
from backend.scripts.diagnose_quality import digest
from backend.scripts.evaluate import _save, verify_findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--video', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--release-cache', action='store_true')
    args = parser.parse_args()
    source = json.loads(Path(args.source).read_text(encoding='utf-8'))
    if Path(args.output).exists():
        parser.error('Control output already exists; preserve it and use another path')
    config = replace(AnalyzerConfig(**source['run_inputs']['config']), release_cuda_cache=args.release_cache)
    started = time.monotonic()
    result = {'source_sha256': digest(args.source), 'video_sha256': digest(args.video),
              'cache_release': args.release_cache, 'config': asdict(config), 'code_sha256': digest(__file__),
              'runtime_sha256': {str(p): digest(p) for p in sorted(Path('backend/analyzer').glob('*.py'))},
              'notice': 'Single control probe, not a quality evaluation.'}
    try:
        report = analyze_video_report(args.video, config)
        result.update(status='evaluated', timings=report.timings, peak_vram_bytes=report.peak_vram_bytes,
                      peak_reserved_vram_bytes=report.peak_reserved_vram_bytes,
                      predictions=verify_findings(report.findings, source['run_inputs']['catalog']))
    except AnalyzerError as exc:
        result.update(status='error', error_type=type(exc).__name__, error=str(exc))
    result['wall_sec'] = time.monotonic() - started
    _save(args.output, result)
    print(json.dumps({k: result[k] for k in ('status', 'wall_sec')}
                     | {'error_type': result.get('error_type')}, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
