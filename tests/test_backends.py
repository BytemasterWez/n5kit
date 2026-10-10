import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from n5kit import backends, docker, host

class BackendTests(unittest.TestCase):
    def test_live_backends_disabled_even_with_inherited_opt_in(self):
        for value in ('0', '1'):
            with patch.dict(os.environ, {'N5KIT_REAL':value}), patch.object(backends.subprocess, 'run') as run, patch.object(backends.subprocess, 'Popen') as spawn, patch.object(Path, 'open') as opened:
                for call in (lambda: docker.call('version'), host.inventory, lambda: host.book('a'*32,60), lambda: backends.commands.Popen(['/bin/true'])):
                    with self.assertRaisesRegex(AssertionError, 'offline_backend'): call()
                for boundary in (run, spawn, opened): boundary.assert_not_called()

    def test_audit_backstop_blocks_raw_calls_and_reservation_alias(self):
        for event, args in [('subprocess.Popen', ('docker', ['docker','version'], None, None)),
                            ('subprocess.Popen', ('/bin/true', ['/bin/true'], None, None)),
                            ('open', ('/home/wez/Work/../Work/HOST-RESERVATIONS.md', 'r+', os.O_RDWR))]:
            with self.assertRaisesRegex(AssertionError, 'offline_backend'): sys.audit(event, *args)
        sys.audit('open', str(Path('/tmp/explicit-unit-fixture')), 'w', os.O_WRONLY)
