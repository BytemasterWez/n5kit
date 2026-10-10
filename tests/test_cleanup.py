import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
from n5kit import run


class CleanupTests(unittest.TestCase):
    def test_invalid_callback_before_admission(self):
        for argv in (None, "true", [], [1], [""], ["bad\0arg"]):
            with self.subTest(argv=argv), patch.object(run.host, 'window', side_effect=AssertionError('admission reached')):
                with self.assertRaisesRegex(RuntimeError, 'invalid_cleanup'):
                    run.submit({'mode': 'native', 'argv': ['true'], 'cleanup_argv': argv}, '/tmp/unused-n5kit')

    def test_callback_order_receipt_and_failure(self):
        argv = ['/usr/bin/cleanup', 'literal; $(no shell)']
        for outcome, service in ((0, 'success'), (0, 'signal'), (7, 'success'), (subprocess.TimeoutExpired(argv, 60), 'success')):
            with self.subTest(outcome=outcome, service=service), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                config = root / 'config.json'
                config.write_text(json.dumps({'mode': 'native', 'token': 'a'*32, 'cleanup_argv': argv}))
                (root / 'job.json').write_text('{"ok": true}')
                order = []
                def execute(args, **kw):
                    order.append('callback')
                    self.assertEqual(args, argv)
                    self.assertFalse(kw['shell'])
                    self.assertEqual(kw['timeout'], 60)
                    self.assertEqual(kw['cwd'], root)
                    for name in ('stdin', 'stdout', 'stderr'):
                        self.assertEqual(kw[name], subprocess.DEVNULL)
                    if isinstance(outcome, Exception):
                        raise outcome
                    return Mock(returncode=outcome)
                with patch.object(run.subprocess, 'run', side_effect=execute), patch.object(run.host, 'release', side_effect=lambda _: order.append('release')), patch.dict(run.os.environ, SERVICE_RESULT=service):
                    if outcome == 0:
                        run.cleanup(config)
                    else:
                        with self.assertRaises((RuntimeError, subprocess.TimeoutExpired)):
                            run.cleanup(config)
                self.assertEqual(order, ['callback', 'release'] if outcome == 0 else ['callback'])
                receipt = json.loads((root / 'cleanup-command.json').read_text())
                self.assertEqual(receipt, {'returncode': outcome if isinstance(outcome, int) else None})
                self.assertEqual(json.loads((root / 'END.json').read_text())['ok'], outcome == 0 and service == 'success')
