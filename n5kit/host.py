"""Strict host admission and lock-protected shared reservations."""
import datetime as dt
import fcntl
import os
import re
from pathlib import Path
from .backends import commands as subprocess
from . import require, token_ok
from .backends import real

RESERVATIONS = Path('/home/wez/Work/HOST-RESERVATIONS.md')
FLOORS = {'memory': 16, 'docker': 256, 'work': 100}


def metrics():
    mem = next(int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
    result = {'memory': mem / 2**20, 'load': os.getloadavg()[0]}
    for key, path in [('docker', '/var/lib/docker'), ('work', '/home/wez/Work')]:
        stat = os.statvfs(path)
        result[key] = stat.f_bavail * stat.f_frsize / 2**30
    return result


def probe(floors=None, exception=None):
    try:
        values = metrics()
        minimum = FLOORS
        if exception is not None:
            require(isinstance(exception, dict), 'invalid_exception')
            require(all(isinstance(exception.get(k), str) and exception[k].strip() for k in ('authority', 'card', 'expiry')), 'invalid_exception')
            expiry = dt.datetime.fromisoformat(exception['expiry'])
            remaining = (expiry - dt.datetime.now(dt.timezone.utc)).total_seconds()
            require(0 < remaining <= 7200, 'exception_expiry')
            minimum = FLOORS | {'memory': 8}
        limits = FLOORS | (floors or {})
        require(set(limits) == set(FLOORS) and all(limits[k] >= v for k, v in minimum.items()), 'weakened_floor')
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
    result = {}
    env = dict(os.environ, TZ='UTC', LC_ALL='C')
    for kind, args in [('system', []), ('user', ['--user'])]:
        raw = subprocess.check_output(['systemctl', *args, 'list-timers', '--all', '--no-legend', '--no-pager'], env=env, timeout=10).decode()
        active = {}
        for row in raw.splitlines():
            fields = row.split()
            if any(word in ' '.join(fields[-2:]) for word in ('snapper-', 'backup', 'restore-verify')):
                active[fields[-1]] = subprocess.check_output(['systemctl', *args, 'show', fields[-1], '--property=ActiveState', '--value'], timeout=5).decode().strip()
        result[kind] = {'timers': raw, 'active': active}
    return result


def scheduled(timers, now, seconds):
    require(set(timers) == {'system', 'user'}, 'incomplete_timer_inventory')
    for scope in timers.values():
        require(all(state in ('inactive', 'failed') for state in scope['active'].values()), 'conflicting_job_active')
        for row in scope['timers'].splitlines():
            fields = row.split()
            if not any(word in ' '.join(fields[-2:]) for word in ('snapper-', 'backup', 'restore-verify')):
                continue
            require(len(fields) >= 6 and fields[3] == 'UTC' and re.fullmatch(r'\d{4}-\d{2}-\d{2}', fields[1]), 'unknown_timer_deadline')
            due = dt.datetime.fromisoformat(fields[1] + 'T' + fields[2] + '+00:00')
            require(due > now + dt.timedelta(seconds=seconds), 'conflicting_timer_due')


def reservation(token, seconds, path=RESERVATIONS):
    token_ok(token)
    if path.resolve() == RESERVATIONS: real()
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
