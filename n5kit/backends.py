"""Real effects are opt-in; unittest processes additionally have an audit backstop."""
import os
from pathlib import Path
import subprocess
import sys


def offline():
    return os.environ.get('N5KIT_OFFLINE') == '1' or 'unittest' in sys.modules


def real():
    if offline() or os.environ.get('N5KIT_REAL') != '1':
        raise AssertionError('offline_backend')


class Commands:
    DEVNULL = subprocess.DEVNULL

    def __getattr__(self, name):
        def invoke(*args, **kwargs):
            real()
            return getattr(subprocess, name)(*args, **kwargs)
        return invoke


def audit(event, args):
    if not offline(): return
    if event == 'subprocess.Popen':
        argv = args[1]
        if not (isinstance(argv, (list, tuple)) and Path(args[0]).name == Path(argv[0]).name and (Path(argv[0]).name == 'git' or list(argv[:4]) == [sys.executable, '-m', 'unittest', 'discover'])):
            raise AssertionError('offline_backend')
    if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
        path = Path(os.fsdecode(args[0])).resolve()
        if path in (Path('/home/wez/Work/HOST-RESERVATIONS.md'), Path('/home/wez/Work/.ai-continuity.lock')) and args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            raise AssertionError('offline_backend')


commands = Commands()
sys.addaudithook(audit)
