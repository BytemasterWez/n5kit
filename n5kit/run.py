"""Transient SERVICE owns the worker; ExecStopPost owns cleanup even after SIGKILL."""
import datetime as dt
import json
import os
from pathlib import Path
import re
import shlex
from .backends import commands as subprocess
import sys
import time
import uuid
from . import docker, evidence, host, require, token_ok


def submit(spec, root, token=None):
    mode, argv = spec.get('mode', 'docker'), spec.get('argv')
    require(mode in ('docker', 'native') and (mode != 'native' or
            isinstance(argv, list) and bool(argv) and all(isinstance(a, str) and a and '\0' not in a for a in argv)), 'invalid_job')
    token = token or uuid.uuid4().hex
    token_ok(token)
    seconds = spec.get('seconds', 60)
    memory, tasks, seconds = native_limits(spec) if mode == 'native' else ('256M', 64, seconds)
    require(mode == 'native' or 0 < seconds <= 1800, 'runtime_limit')
    host.window(dt.datetime.now().astimezone(), seconds + 90)
    host.probe(spec.get('floors'))
    timers = host.inventory()
    host.scheduled(timers, dt.datetime.now().astimezone(), seconds + 90)
    root = Path(root).resolve()
    require(re.fullmatch(r'[/A-Za-z0-9_.-]+', str(root)) is not None, 'unsafe_unit_path')
    root.mkdir(parents=True, exist_ok=True)
    out = root / token
    out.mkdir(mode=0o700)
    output = out / 'output'
    output.mkdir(mode=0o700)
    config = out / 'config.json'
    evidence.receipt(config, dict(spec, token=token, uid=os.getuid(), gid=os.getgid()))
    evidence.receipt(out / 'timers.json', timers)
    unit = 'n5kit-' + token
    command = [sys.executable, '-m', 'n5kit.run']
    stop = shlex.join(command + ['cleanup', str(config)])
    properties = [f'RuntimeMaxSec={seconds}', 'TimeoutStopSec=90', f'MemoryMax={memory}', f'TasksMax={tasks}',
                  'KillMode=control-group', 'SendSIGKILL=yes', f'ExecStopPost={stop}', 'UMask=0077']
    args = ['systemd-run', '--user', '--no-ask-password', '--wait', '--collect', '--quiet', '--setenv=N5KIT_REAL=1', '--setenv=N5KIT_OFFLINE=0', f'--unit={unit}',
            f'--working-directory={Path(__file__).resolve().parent.parent}']
    args += [f'--property={value}' for value in properties]
    host.window(dt.datetime.now().astimezone(), seconds + 90)
    host.book(token, seconds + 90)
    try:
        evidence.receipt(out / 'START.json', {'at': dt.datetime.now().astimezone().isoformat(), 'unit': unit, **scope(spec)})
        result = subprocess.run(args + command + ['worker', str(config)], timeout=seconds+120)
        require(result.returncode == 0 and (out / 'END.json').exists(), 'run_failed')
        end = json.loads((out / 'END.json').read_text())
        require(end['ok'], 'cleanup_failed')
    except BaseException:
        subprocess.run(['systemctl', '--user', 'stop', unit], capture_output=True, timeout=100)
        if not (out / 'END.json').exists():
            cleanup(config)
        raise
    return out


def native_limits(spec):
    values = (spec.get('memory', 256*2**20), spec.get('pids', 64), spec.get('seconds', 60))
    require(all(type(v) is int and 0 < v <= cap for v, cap in zip(values, (8*2**30, 256, 14400))), 'native_limits')
    return values


def scope(spec):
    caps = dict(zip(('memory_bytes', 'tasks', 'seconds'), native_limits(spec))) if spec.get('mode') == 'native' else {}
    return {'caps': caps, 'mode': spec.get('mode', 'docker'), 'limits_scope': 'unit job and descendants; external services excluded' if spec.get('mode') == 'native' else 'Docker flags bound container; unit bounds worker'}


def native_sample(job, token):
    group = Path('/sys/fs/cgroup') / Path('/proc/self/cgroup').read_text().strip().split('0::', 1)[1].lstrip('/')
    require(group.name == 'n5kit-' + token + '.service', 'native_outside_unit')
    status = job.poll()
    events = dict(line.split() for line in (group / 'memory.events').read_text().splitlines())
    require(int(events['oom']) == 0 and int(events['oom_kill']) == 0, 'native_oom')
    descendants = [int(p) for p in (group / 'cgroup.procs').read_text().split() if int(p) != os.getpid()]
    return {'running': status is None, 'exit': status, 'peak': int((group / 'memory.peak').read_text()),
            'pids': int((group / 'pids.current').read_text()), 'descendants': descendants}


def worker(config):
    spec = json.loads(config.read_text())
    out, token = config.parent, spec['token']
    host.window(dt.datetime.now().astimezone(), spec.get('seconds', 60) + 90)
    host.scheduled(host.inventory(), dt.datetime.now().astimezone(), spec.get('seconds', 60) + 90)
    host.probe(spec.get('floors'))
    native = spec.get('mode') == 'native'
    if native:
        job = subprocess.Popen(spec['argv'], shell=False, cwd=out / 'output', stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        network = docker.create('network', token)
        volume = docker.create('volume', token)
        job = docker.container(token, spec['image'], out / 'output', spec['argv'], spec.get('memory', 512*2**20),
                               spec.get('pids', 64), network=network, volume=volume)
    start = previous = time.monotonic()
    while True:
        floors = dict(spec.get('floors', host.FLOORS))
        if spec.get('rehearsal_memory_after') is not None and time.monotonic() - start >= spec['rehearsal_memory_after']:
            floors['memory'] = 10**9
        values = host.probe(floors)
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


def cleanup(config):
    spec = json.loads(config.read_text())
    out = config.parent
    try:
        removed = [] if spec.get('mode') == 'native' else docker.cleanup(spec['token'], spec['image'], out / 'output', spec['uid'], spec['gid'])
        host.release(spec['token'])
        job = json.loads((out / 'job.json').read_text()) if (out / 'job.json').exists() else {}
        ok = os.environ.get('SERVICE_RESULT', 'failed') == 'success' and (out / 'job.json').exists()
        evidence.receipt(out / 'END.json', {'at': dt.datetime.now().astimezone().isoformat(), 'ok': ok,
                                          'service_result': os.environ.get('SERVICE_RESULT', 'failed'), 'removed': removed, 'orphans_killed': job.get('orphans_killed'), **scope(spec)})
    except Exception:
        evidence.receipt(out / 'cleanup-failed.json', {'ok': False})
        evidence.receipt(out / 'END.json', {'at': dt.datetime.now().astimezone().isoformat(), 'ok': False, 'cleanup_failed': True})
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
