"""All mutations address resources bearing the exact n5kit ownership label."""
import json
import os
import re
from pathlib import Path
from .backends import commands as subprocess
from . import boundary, require, token_ok

LABEL = 'n5kit.token'


def call(*args, timeout=15):
    result = subprocess.run(['docker', *args], capture_output=True, timeout=timeout)
    require(result.returncode == 0, 'docker_command_failed')
    return result.stdout.decode().strip()


def inspect(kind, name, token):
    token_ok(token)
    item = json.loads(call(kind, 'inspect', name))[0]
    labels = item['Config']['Labels'] if kind == 'container' else item['Labels']
    require(labels.get(LABEL) == token, 'foreign_resource')
    return item


def owned(kind, token):
    token_ok(token)
    flags = '-aq' if kind == 'container' else '-q'
    names = call(kind, 'ls', flags, '--filter', f'label={LABEL}={token}').splitlines()
    for name in names:
        inspect(kind, name, token)
    return names


def create(kind, token, public=False):
    token_ok(token)
    name = f'n5kit-{token}-{kind}'
    flags = ['--internal'] if kind == 'network' and not public else []
    call(kind, 'create', *flags, '--label', f'{LABEL}={token}', name)
    inspect(kind, name, token)
    return name


def container(token, image, output, argv, memory=512*2**20, pids=64, role='job', network='none', volume=None, policy=None):
    token_ok(token)
    require(re.fullmatch(r'sha256:[0-9a-f]{64}', image) is not None, 'immutable_image_required')
    require(memory > 0 and pids > 0 and role in ('job', 'ownership'), 'container_limits')
    mounts = ['--mount', f'type=bind,src={output},dst=/output']
    if volume:
        inspect('volume', volume, token)
        mounts += ['--mount', f'type=volume,src={volume},dst=/scratch']
    if network != 'none':
        inspect('network', network, token)
    name = f'n5kit-{token}-{role}'
    cid = call('create', '--pull=never', '--name', name, '--label', f'{LABEL}={token}', '--network', network,
               '--memory', str(memory), '--memory-swap', str(memory), '--pids-limit', str(pids), '--cpus', '1',
               '--restart', 'no', '--security-opt', 'no-new-privileges:true', *mounts, *(boundary.docker(policy, output) if policy else []), '--entrypoint', argv[0], image, *argv[1:])
    inspect('container', cid, token)
    call('start', cid)
    return cid


def sample(cid, token):
    item = inspect('container', cid, token)
    state = item['State']
    require(not state['OOMKilled'], 'container_oom')
    result = {'running': state['Running'], 'exit': state['ExitCode']}
    if state['Running']:
        try:
            relative = Path(f"/proc/{state['Pid']}/cgroup").read_text().strip().split('::')[1].lstrip('/')
            root = Path('/sys/fs/cgroup') / relative
            events = dict(line.split() for line in (root / 'memory.events').read_text().splitlines())
            require(int(events['oom']) == 0 and int(events['oom_kill']) == 0, 'container_oom_events')
            result.update(peak=int((root / 'memory.peak').read_text()), pids=int((root / 'pids.peak').read_text()))
        except OSError:
            terminal = inspect('container', cid, token)['State']
            require(not terminal['Running'] and not terminal['OOMKilled'], 'unreadable_live_cgroup')
            result = {'running': False, 'exit': terminal['ExitCode']}
    return result


def cleanup(token, image, output, uid=None, gid=None):
    removed = []
    for cid in owned('container', token):
        call('stop', '--time', '5', cid, timeout=10)
    uid, gid = os.getuid() if uid is None else uid, os.getgid() if gid is None else gid
    error = None
    try:
        if any(Path(output).iterdir()):
            cid = container(token, image, output, ['chown', '-hR', f'{uid}:{gid}', '/output'], role='ownership')
            require(call('wait', cid, timeout=15) == '0', 'ownership_failed')
    except Exception as caught:
        error = caught
    for kind in ('container', 'network', 'volume'):
        for name in owned(kind, token):
            try:
                call(kind, 'rm', *(['-f'] if kind == 'container' else []), name)
                removed.append({'kind': kind, 'id': name})
            except Exception as caught:
                error = caught
        require(not owned(kind, token), 'cleanup_leak')
    require(error is None, 'cleanup_failed')
    return removed
