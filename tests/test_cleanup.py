import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
from n5kit import run


class CleanupTests(unittest.TestCase):
    def setUp(self):
        for name, effect in [('bind', lambda spec, out: spec), ('native', lambda spec, argv, out: argv)]:
            mock = patch.object(run.boundary, name, side_effect=effect)
            mock.start()
            self.addCleanup(mock.stop)

    def test_invalid_callback_before_admission(self):
        for argv in (None, "true", [], [1], [""], ["bad\0arg"]):
            with self.subTest(argv=argv), patch.object(run.host, 'window', side_effect=AssertionError('admission reached')):
                with self.assertRaisesRegex(RuntimeError, 'invalid_cleanup'):
                    run.submit({'mode': 'native', 'argv': ['true'], 'cleanup_argv': argv}, '/tmp/unused-n5kit')

    def test_cleanup_native_only_and_timeout_bounds(self):
        specs = [{'mode': 'docker', 'cleanup_argv': ['true']}, {'mode': 'native', 'cleanup_timeout': 1}]
        specs += [dict(mode='native', cleanup_argv=['true'], cleanup_timeout=v) for v in (True, 0, -1, '1', 1.5, 721)]
        for spec in specs:
            with self.subTest(spec=spec), patch.object(run.host, 'window', side_effect=AssertionError('admission reached')):
                with self.assertRaisesRegex(RuntimeError, 'invalid_cleanup'):
                    run.submit(dict(spec, argv=['true']), '/tmp/unused-n5kit')

    def test_cleanup_time_is_reserved_and_bounded_in_unit(self):
        with tempfile.TemporaryDirectory() as temp:
            def execute(args, **kw):
                self.assertIn('--property=TimeoutStopSec=810', args)
                self.assertEqual(kw['timeout'], 900)
                (Path(temp)/('a'*32)/'END.json').write_text('{"ok":true}')
                return Mock(returncode=0)
            with patch.object(run.host, 'window') as window, patch.object(run.host, 'probe'), patch.object(run.host, 'inventory', return_value={}), patch.object(run.host, 'scheduled') as scheduled, patch.object(run.host, 'book') as book, patch.object(run.subprocess, 'run', side_effect=execute):
                run.submit(dict(mode='native', argv=['true'], cleanup_argv=['cleanup'], cleanup_timeout=720), temp, 'a'*32)
                self.assertEqual([c.args[1] for c in window.call_args_list], [870, 870])
                self.assertEqual(scheduled.call_args.args[2], 870)
                book.assert_called_once_with('a'*32, 870)
            config = Path(temp)/('a'*32)/'config.json'
            with patch.object(run.host, 'window') as window, patch.object(run.host, 'inventory', return_value={}), patch.object(run.host, 'scheduled') as scheduled, patch.object(run.host, 'probe', side_effect=RuntimeError('worker boundary')):
                with self.assertRaisesRegex(RuntimeError, 'worker boundary'):
                    run.worker(config)
                self.assertEqual(window.call_args.args[1], 870)
                self.assertEqual(scheduled.call_args.args[2], 870)

    def test_callback_order_receipt_and_failure(self):
        argv = ['/usr/bin/cleanup', 'literal; $(no shell)']
        for outcome, service, cap in ((0, 'success', 60), (0, 'signal', 720), (7, 'success', 60), (subprocess.TimeoutExpired(argv, 60), 'success', 60)):
            with self.subTest(outcome=outcome, service=service), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                config = root / 'config.json'
                config.write_text(json.dumps({'mode': 'native', 'token': 'a'*32, 'cleanup_argv': argv, 'cleanup_timeout': cap}))
                (root / 'job.json').write_text('{"ok": true}')
                order = []
                def execute(args, **kw):
                    order.append('callback')
                    (root/'cleanup-result.json').write_text('{"destroy_absent":true}')
                    self.assertEqual(args, argv)
                    self.assertFalse(kw['shell'])
                    self.assertEqual(kw['timeout'], cap)
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
                end = json.loads((root / 'END.json').read_text())
                self.assertEqual(end['ok'], outcome == 0 and service == 'success')
                self.assertEqual(end['cleanup_evidence'], {'file': 'cleanup-result.json', 'sha256': hashlib.sha256(b'{"destroy_absent":true}').hexdigest()})

    def test_cleanup_receipt_symlink_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root/'config.json'
            config.write_text(json.dumps(dict(mode='native', token='a'*32, cleanup_argv=['true'])))
            (root/'cleanup-result.json').symlink_to(root/'missing')
            with patch.object(run.subprocess, 'run', return_value=Mock(returncode=0)), patch.object(run.host, 'release') as release:
                with self.assertRaisesRegex(RuntimeError, 'cleanup_evidence_symlink'):
                    run.cleanup(config)
                release.assert_not_called()
            self.assertFalse(json.loads((root/'END.json').read_text())['ok'])
