"""Transient SERVICE owns the worker; ExecStopPost owns cleanup even after SIGKILL."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
from .backends import commands as subprocess
import sys
import time
import uuid
from . import boundary, docker, evidence, host, require, token_ok


def submit(spec, root, token=None):
    mode, argv = spec.get('mode', 'docker'), spec.get('argv')
    require(mode in ('docker', 'native') and (mode != 'native' or
            isinstance(argv, list) and bool(argv) and all(isinstance(a, str) and a and '\0' not in a for a in argv)), 'invalid_job')
    cleanup_cap = spec.get('cleanup_timeout', 60) if 'cleanup_argv' in spec else 0
    require('cleanup_argv' in spec or 'cleanup_timeout' not in spec, 'invalid_cleanup')
    if 'cleanup_argv' in spec:
        cleanup_args = spec['cleanup_argv']
        require(mode == 'native' and type(cleanup_cap) is int and 0 < cleanup_cap <= 720 and isinstance(cleanup_args, list) and bool(cleanup_args) and all(isinstance(a, str) and a and '\0' not in a for a in cleanup_args), 'invalid_cleanup')
    token = token or uuid.uuid4().hex
    token_ok(token)
    seconds = spec.get('seconds', 60)
    memory, tasks, seconds = native_limits(spec) if mode == 'native' else ('256M', 64, seconds)
    require(mode == 'native' or 0 < seconds <= 1800, 'runtime_limit')
    spec = boundary.bind(spec, root)
    host.window(dt.datetime.now().astimezone(), seconds + cleanup_cap + 90)
    host.probe(spec.get('floors'), spec.get('exception'))
    timers = host.inventory()
    host.scheduled(timers, dt.datetime.now().astimezone(), seconds + cleanup_cap + 90)
    root = Path(root).resolve()
    require(re.fullmatch(r'[/A-Za-z0-9_.-]+', str(root)) is not None, 'unsafe_unit_path')
    root.mkdir(parents=True, exist_ok=True)
    out = root / token
    out.mkdir(mode=0o700)
    output = out / 'output'
    output.mkdir(mode=0o700)
    config = out / 'config.json'
    evidence.receipt(config, dict(spec, token=token, uid=os.getuid(), gid=os.getgid()))
    digest = hashlib.sha256(config.read_bytes()).hexdigest()
    evidence.receipt(out / 'timers.json', timers)
    unit = 'n5kit-' + token
    command = [sys.executable, '-m', 'n5kit.run']
    stop = shlex.join(command + ['cleanup', str(config)])
    properties = [f'RuntimeMaxSec={seconds}', f'TimeoutStopSec={cleanup_cap + 90}', f'MemoryMax={memory}', f'TasksMax={tasks}',
                  'KillMode=control-group', 'SendSIGKILL=yes', f'ExecStopPost={stop}', 'UMask=0077']
    args = ['systemd-run', '--user', '--no-ask-password', '--wait', '--collect', '--quiet', '--setenv=N5KIT_REAL=1', '--setenv=N5KIT_OFFLINE=0', '--setenv=N5KIT_CONFIG_SHA256='+digest, f'--unit={unit}',
            f'--working-directory={Path(__file__).resolve().parent.parent}']
    args += [f'--property={value}' for value in properties]
    host.window(dt.datetime.now().astimezone(), seconds + cleanup_cap + 90)
    host.book(token, seconds + cleanup_cap + 90)
    try:
        evidence.receipt(out / 'START.json', {'at': dt.datetime.now().astimezone().isoformat(), 'unit': unit, **scope(spec)})
        result = subprocess.run(args + command + ['worker', str(config)], timeout=seconds+cleanup_cap+120)
        require(result.returncode == 0 and (out / 'END.json').exists(), 'run_failed')
        end = json.loads((out / 'END.json').read_text())
        require(end['ok'], 'cleanup_failed')
    except BaseException:
        subprocess.run(['systemctl', '--user', 'stop', unit], capture_output=True, timeout=cleanup_cap+100)
        if not (out / 'END.json').exists():
            cleanup(config, digest)
        raise
    return out


def native_limits(spec):
    values = (spec.get('memory', 256*2**20), spec.get('pids', 64), spec.get('seconds', 60))
    require(all(type(v) is int and 0 < v <= cap for v, cap in zip(values, (8*2**30, 256, 14400))), 'native_limits')
    return values


def scope(spec):
    caps = dict(zip(('memory_bytes', 'tasks', 'seconds'), native_limits(spec))) if spec.get('mode') == 'native' else {}
    return {'boundary': spec.get('_boundary'), 'exception': spec.get('exception'), 'caps': caps, 'mode': spec.get('mode', 'docker'), 'limits_scope': 'unit job and descendants; external services excluded' if spec.get('mode') == 'native' else 'Docker flags bound container; unit bounds worker'}


def native_sample(job, token):
    group = Path('/sys/fs/cgroup') / Path('/proc/self/cgroup').read_text().strip().split('0::', 1)[1].lstrip('/')
    require(group.name == 'n5kit-' + token + '.service', 'native_outside_unit')
    status = job.poll()
    events = dict(line.split() for line in (group / 'memory.events').read_text().splitlines())
    require(int(events['oom']) == 0 and int(events['oom_kill']) == 0, 'native_oom')
    descendants = [int(p) for p in (group / 'cgroup.procs').read_text().split() if int(p) != os.getpid()]
    return {'running': status is None, 'exit': status, 'peak': int((group / 'memory.peak').read_text()),
            'pids': int((group / 'pids.peak').read_text()), 'pids_current': int((group / 'pids.current').read_text()), 'descendants': descendants}


def worker(config):
    spec = json.loads(config.read_text())
    out, token = config.parent, spec['token']
    cleanup_cap = spec.get('cleanup_timeout', 60) if 'cleanup_argv' in spec else 0
    host.window(dt.datetime.now().astimezone(), spec.get('seconds', 60) + cleanup_cap + 90)
    host.scheduled(host.inventory(), dt.datetime.now().astimezone(), spec.get('seconds', 60) + cleanup_cap + 90)
    host.probe(spec.get('floors'), spec.get('exception'))
    native = spec.get('mode') == 'native'
    if native:
        evidence.receipt(out / 'worker-baseline.json', {'worker_pid': os.getpid(), 'worker_tasks': len(list(Path('/proc/self/task').iterdir()))})
        job = subprocess.Popen(boundary.native(spec, spec['argv'], out / 'output'), shell=False, cwd=out / 'output', stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        network = docker.create('network', token, bool(spec['_boundary']['policy']['network_hosts']['allowed']))
        volume = docker.create('volume', token)
        job = docker.container(token, spec['image'], out / 'output', spec['argv'], spec.get('memory', 512*2**20),
                               spec.get('pids', 64), network=network, volume=volume, policy=spec['_boundary'])
    start = previous = time.monotonic()
    while True:
        floors = dict(spec.get('floors', host.FLOORS))
        if spec.get('rehearsal_memory_after') is not None and time.monotonic() - start >= spec['rehearsal_memory_after']:
            floors['memory'] = 10**9
        values = host.probe(floors, spec.get('exception'))
        sample = native_sample(job, token) if native else docker.sample(job, token)
        now = time.monotonic()
        require(now - previous <= 30, 'sample_gap')
        with (out / 'samples.jsonl').open('a') as stream:
            stream.write(json.dumps({'at': time.time(), 'gap': now-previous, 'host': values, ('native' if native else 'container'): sample}) + '\n')
        previous = now
        if not sample['running']:
            require(sample['exit'] == 0, 'job_failed')
            evidence.receipt(out / 'job.json', {'ok': True, 'elapsed': now-start, 'orphans_killed': len(sample['descendants']) if native else None})
            return
        time.sleep(max(0, 5 - (time.monotonic() - now)))


def cleanup(config, expected=None):
    raw = config.read_bytes()
    require(not config.is_symlink() and hashlib.sha256(raw).hexdigest() == (expected or os.environ.get('N5KIT_CONFIG_SHA256')), 'config_changed')
    spec = json.loads(raw)
    out = config.parent
    detail = None
    try:
        removed = [] if spec.get('mode') == 'native' else docker.cleanup(spec['token'], spec['image'], out / 'output', spec['uid'], spec['gid'])
        if 'cleanup_argv' in spec:
            status = None
            try:
                status = subprocess.run(boundary.native(spec, spec['cleanup_argv'], out), shell=False, cwd=out, timeout=spec.get('cleanup_timeout', 60),
                                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
            finally:
                evidence.receipt(out / 'cleanup-command.json', {'returncode': status})
                path = out / 'cleanup-result.json'
                require(not path.is_symlink(), 'cleanup_evidence_symlink')
                if path.exists():
                    detail = {'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            require(status == 0, 'cleanup_command_failed')
        host.release(spec['token'])
        job = json.loads((out / 'job.json').read_text()) if (out / 'job.json').exists() else {}
        ok = os.environ.get('SERVICE_RESULT', 'failed') == 'success' and (out / 'job.json').exists()
        evidence.receipt(out / 'END.json', {'at': dt.datetime.now().astimezone().isoformat(), 'ok': ok,
                                          'service_result': os.environ.get('SERVICE_RESULT', 'failed'), 'removed': removed, 'cleanup_evidence': detail, 'orphans_killed': job.get('orphans_killed'), **scope(spec)})
    except Exception:
        evidence.receipt(out / 'cleanup-failed.json', {'ok': False})
        evidence.receipt(out / 'END.json', {'at': dt.datetime.now().astimezone().isoformat(), 'ok': False, 'cleanup_failed': True, 'cleanup_evidence': detail, **scope(spec)})
        raise


if __name__ == '__main__':
    mode = ''
    try:
        mode, path = sys.argv[1:3]
        if mode in ('worker', 'cleanup'):
            globals()[mode](Path(path))
        else:
            require(mode == 'start', 'unknown_command')
            print(submit(json.loads(Path(path).read_text()), sys.argv[3]))
    except Exception as error:
        if mode == 'worker':
            evidence.receipt(Path(path).parent / 'failure.json', {'error': str(error) if isinstance(error, RuntimeError) else type(error).__name__, 'cause': str(error.__cause__) if isinstance(error.__cause__, RuntimeError) else None})
        print(type(error).__name__, file=sys.stderr)
        sys.exit(1)
