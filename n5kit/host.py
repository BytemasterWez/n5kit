"""Strict host admission and lock-protected shared reservations."""
import datetime as dt
import fcntl
import os
from pathlib import Path
import subprocess
from . import require, token_ok

RESERVATIONS = Path('/home/wez/Work/HOST-RESERVATIONS.md')
FLOORS = {'memory': 16, 'docker': 256, 'work': 100}


def metrics():
    mem = next(int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
    result = {'memory': mem / 2**20, 'load': os.getloadavg()[0]}
    for key, path in [('docker', '/var/lib/docker'), ('work', '/home/wez/Work')]:
        stat = os.statvfs(path)
        result[key] = stat.f_bavail * stat.f_frsize / 2**30
    return result


def probe(floors=None):
    try:
        values = metrics()
        limits = FLOORS | (floors or {})
        require(set(limits) == set(FLOORS) and all(limits[k] >= v for k, v in FLOORS.items()), 'weakened_floor')
        for key, floor in limits.items():
            require(values[key] > floor, 'host_' + key)
        require(values['load'] < 2 * (os.cpu_count() or 1), 'host_load')
        return values
    except Exception as error:
        raise RuntimeError('host_guard_failed') from error


def window(now, seconds):
    require(5 <= now.minute < 52, 'snapshot_window')
    require(seconds > 0 and now + dt.timedelta(seconds=seconds) < now.replace(minute=52, second=0, microsecond=0), 'window_fit')


def inventory():
    return {kind: subprocess.check_output(['systemctl', *args, 'list-timers', '--all', '--no-pager'], timeout=10).decode()
            for kind, args in [('system', []), ('user', ['--user'])]}


def reservation(token, seconds, path=RESERVATIONS):
    token_ok(token)
    with (path.parent / '.ai-continuity.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        text = path.read_text()
        rows = [line for line in text.splitlines() if line.startswith('|') and not line.startswith(('| Owner', '|---'))]
        if seconds is not None:
            require(not rows, 'host_booked')
            now = dt.datetime.now().astimezone()
            end = now + dt.timedelta(seconds=seconds)
            text += f'| n5kit {token} | #0228 | {now.isoformat()} | {end.isoformat()} | guarded job |\n'
        else:
            text = '\n'.join(line for line in text.splitlines() if not line.startswith(f'| n5kit {token} |')) + '\n'
        with path.open('r+') as stream:
            stream.write(text)
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())


def book(token, seconds, path=RESERVATIONS):
    reservation(token, seconds, path)


def release(token, path=RESERVATIONS):
    reservation(token, None, path)
