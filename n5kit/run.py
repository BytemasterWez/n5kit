"""Transient SERVICE owns the worker; ExecStopPost owns cleanup even after SIGKILL."""
import datetime as dt
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import uuid
from . import docker, evidence, host, require, token_ok


def submit(spec, root, token=None):
    token = token or uuid.uuid4().hex
    token_ok(token)
    seconds = spec.get('seconds', 60)
    require(0 < seconds <= 1800, 'runtime_limit')
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
    properties = [f'RuntimeMaxSec={seconds}', 'TimeoutStopSec=90', 'MemoryMax=256M', 'TasksMax=64',
                  'KillMode=control-group', 'SendSIGKILL=yes', f'ExecStopPost={stop}', 'UMask=0077']
    args = ['systemd-run', '--user', '--no-ask-password', '--wait', '--collect', '--quiet', f'--unit={unit}',
            f'--working-directory={Path(__file__).resolve().parent.parent}']
    args += [f'--property={value}' for value in properties]
    host.window(dt.datetime.now().astimezone(), seconds + 90)
    host.book(token, seconds + 90)
    try:
        evidence.receipt(out / 'START.json', {'at': dt.datetime.now().astimezone().isoformat(), 'unit': unit})
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


def worker(config):
    spec = json.loads(config.read_text())
    out, token = config.parent, spec['token']
    host.window(dt.datetime.now().astimezone(), spec.get('seconds', 60) + 90)
    host.scheduled(host.inventory(), dt.datetime.now().astimezone(), spec.get('seconds', 60) + 90)
    host.probe(spec.get('floors'))
    network = docker.create('network', token)
    volume = docker.create('volume', token)
    cid = docker.container(token, spec['image'], out / 'output', spec['argv'], spec.get('memory', 512*2**20),
                           spec.get('pids', 64), network=network, volume=volume)
    start = previous = time.monotonic()
    while True:
        floors = dict(spec.get('floors', host.FLOORS))
        if spec.get('rehearsal_memory_after') is not None and time.monotonic() - start >= spec['rehearsal_memory_after']:
            floors['memory'] = 10**9
        values = host.probe(floors)
        sample = docker.sample(cid, token)
        now = time.monotonic()
        require(now - previous <= 30, 'sample_gap')
        with (out / 'samples.jsonl').open('a') as stream:
            stream.write(json.dumps({'at': time.time(), 'gap': now-previous, 'host': values, 'container': sample}) + '\n')
        previous = now
        if not sample['running']:
            require(sample['exit'] == 0, 'job_failed')
            evidence.receipt(out / 'job.json', {'ok': True, 'elapsed': now-start})
            return
        time.sleep(max(0, 5 - (time.monotonic() - now)))


def cleanup(config):
    spec = json.loads(config.read_text())
    out = config.parent
    try:
        removed = docker.cleanup(spec['token'], spec['image'], out / 'output', spec['uid'], spec['gid'])
        host.release(spec['token'])
        ok = os.environ.get('SERVICE_RESULT', 'failed') == 'success' and (out / 'job.json').exists()
        evidence.receipt(out / 'END.json', {'at': dt.datetime.now().astimezone().isoformat(), 'ok': ok,
                                          'service_result': os.environ.get('SERVICE_RESULT', 'failed'), 'removed': removed})
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
