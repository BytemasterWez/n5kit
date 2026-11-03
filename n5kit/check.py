"""One local gate: preflight, lint, archive tests, killed mutations and secret scan."""
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from . import boundary, evidence, require


def scan(tree):
    patterns = [r'-----BEGIN [A-Z ]*PRIVATE' + r' KEY-----', r'(?:sk-proj-|ghp_)[A-Za-z0-9]{20,}',
                r'postgres(?:ql)?://[^\s]+:[^\s]+@']
    for path in tree.rglob('*'):
        if path.is_file() and '.git' not in path.parts:
            require(not path.is_symlink(), 'scan_symlink')
            data = path.read_bytes().decode('utf-8', errors='replace')
            require(not any(re.search(pattern, data) for pattern in patterns), 'secret_scan_failed')


def environment(tree):
    dependency = '' if (tree / 'n5kit').is_dir() else os.pathsep + str(Path(__file__).resolve().parent.parent)
    return dict(os.environ, PYTHONPATH=str(tree) + dependency, PYTHONDONTWRITEBYTECODE='1', N5KIT_REAL='0', N5KIT_OFFLINE='1')


def tests(tree):
    return subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests'], cwd=tree, env=environment(tree),
                          capture_output=True, timeout=60)


def runner_config(tree):
    """Opt-in (#0434): a committed n5kit-check.json replaces the per-mutant loop with the project's own runner."""
    path = tree / 'n5kit-check.json'
    if not path.exists(): return None
    try: config = json.loads(path.read_text())
    except ValueError: config = None
    require(isinstance(config, dict) and set(config) == {'mutation_runner', 'timeout_s', 'reason'}, 'mutation_runner_invalid')
    argv, timeout = config['mutation_runner'], config['timeout_s']
    require(isinstance(argv, list) and argv and all(isinstance(a, str) and a for a in argv), 'mutation_runner_invalid')
    require(isinstance(timeout, int) and 60 <= timeout <= 7200, 'mutation_runner_invalid')
    require(isinstance(config['reason'], str) and config['reason'].strip(), 'mutation_runner_invalid')
    return config


def external(tree, config):
    started = time.monotonic()
    process = subprocess.Popen(config['mutation_runner'], cwd=tree, env=environment(tree), stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, start_new_session=True)
    try:
        output, _ = process.communicate(timeout=config['timeout_s'])
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise RuntimeError('mutation_runner_failed:timeout')
    sys.stderr.write(output.decode(errors='replace'))
    tail = output[-4096:].decode(errors='replace')
    require(process.returncode == 0, 'mutation_runner_failed:rc=' + str(process.returncode))
    return dict(argv=config['mutation_runner'], reason=config['reason'], rc=process.returncode, seconds=round(time.monotonic() - started), output=tail)


def main(repo, receipt_path):
    require(all(shutil.which(tool) for tool in ('git', os.environ.get('RUFF', 'ruff'))), 'missing_check_tools')
    if Path('/home/wez/Work/AI-CONTINUITY.md').exists():
        subprocess.run(['n5-agent-preflight'], check=True)
    with tempfile.TemporaryDirectory(prefix='n5kit-check-') as directory:
        tree, record = evidence.archive(repo, Path(directory))
        subprocess.run([os.environ.get('RUFF', 'ruff'), 'check', str(tree)], check=True)
        scan(tree)
        policy = boundary.load(repo)
        changed = subprocess.check_output(['git', 'diff-tree', '--root', '--no-commit-id', '--name-only', '-r', 'HEAD'], cwd=repo).decode().splitlines()
        diff = {name: (tree/name).read_text(errors='replace') for name in changed if (tree/name).is_file()}
        boundary.scan(tree, policy, diff)
        config = runner_config(tree)
        baseline = tests(tree)
        require(baseline.returncode == 0 and re.search(rb'Ran [1-9][0-9]* tests?', baseline.stderr), 'archive_tests_failed')
        if config:
            rules = json.loads((tree / 'mutations.json').read_text()) if (tree / 'mutations.json').exists() else []
            for rule in rules:
                require((tree / rule['file']).read_text().count(rule['old']) == 1, 'mutation_not_unique')
            result = dict(mutations='external', mutation_runner=external(tree, config))
        else:
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
            result = dict(killed=killed)
        evidence.receipt(receipt_path, dict(record, boundary=policy, ok=True, tests=baseline.stderr.decode(), **result))
        print(json.dumps(dict(record, ok=True, mutations='external' if config else len(killed))))


if __name__ == '__main__':
    main(Path.cwd(), Path(sys.argv[1]))
