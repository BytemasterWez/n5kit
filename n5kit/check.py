"""One local gate: preflight, lint, archive tests, killed mutations and secret scan."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from . import evidence, require


def scan(tree):
    patterns = [r'-----BEGIN [A-Z ]*PRIVATE' + r' KEY-----', r'(?:sk-proj-|ghp_)[A-Za-z0-9]{20,}',
                r'postgres(?:ql)?://[^\s]+:[^\s]+@']
    for path in tree.rglob('*'):
        if path.is_file() and '.git' not in path.parts:
            require(not path.is_symlink(), 'scan_symlink')
            data = path.read_bytes().decode('utf-8', errors='replace')
            require(not any(re.search(pattern, data) for pattern in patterns), 'secret_scan_failed')


def tests(tree):
    dependency = '' if (tree / 'n5kit').is_dir() else os.pathsep + str(Path(__file__).resolve().parent.parent)
    env = dict(os.environ, PYTHONPATH=str(tree) + dependency, PYTHONDONTWRITEBYTECODE='1', N5KIT_REAL='0', N5KIT_OFFLINE='1')
    return subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests'], cwd=tree, env=env,
                          capture_output=True, timeout=60)


def main(repo, receipt_path):
    require(all(shutil.which(tool) for tool in ('git', os.environ.get('RUFF', 'ruff'))), 'missing_check_tools')
    if Path('/home/wez/Work/AI-CONTINUITY.md').exists():
        subprocess.run(['n5-agent-preflight'], check=True)
    with tempfile.TemporaryDirectory(prefix='n5kit-check-') as directory:
        tree, record = evidence.archive(repo, Path(directory))
        subprocess.run([os.environ.get('RUFF', 'ruff'), 'check', str(tree)], check=True)
        scan(tree)
        baseline = tests(tree)
        require(baseline.returncode == 0 and re.search(rb'Ran [1-9][0-9]* tests?', baseline.stderr), 'archive_tests_failed')
        killed = []
        for rule in json.loads((tree / 'mutations.json').read_text()):
            path = tree / rule['file']
            original = path.read_text()
            require(original.count(rule['old']) == 1, 'mutation_not_unique')
            path.write_text(original.replace(rule['old'], rule['new']))
            try:
                require(tests(tree).returncode != 0, 'mutation_survived:' + rule['name'])
                killed.append(rule['name'])
            finally:
                path.write_text(original)
        evidence.receipt(receipt_path, dict(record, ok=True, killed=killed, tests=baseline.stderr.decode()))
        print(json.dumps(dict(record, ok=True, mutations=len(killed))))


if __name__ == '__main__':
    main(Path.cwd(), Path(sys.argv[1]))
