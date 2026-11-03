import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
from n5kit import check

GOOD = {'mutation_runner': ['/usr/bin/python3', 'tests/mutate.py'], 'timeout_s': 3600, 'reason': 'strict runner'}
RULE = [{'name': 'answer', 'file': 'app.py', 'old': 'answer=42', 'new': 'answer=43'}]


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'app.py').write_text('answer=42')
        (self.root / 'mutations.json').write_text(json.dumps(RULE))
        self.receipt = self.root / 'receipt.json'

    def configure(self, value=GOOD):
        (self.root / 'n5kit-check.json').write_text(value if isinstance(value, str) else json.dumps(value))

    def process(self, output=b'0 survived; 3/3 killed', returncode=0):
        return Mock(returncode=returncode, communicate=Mock(return_value=(output, None)))

    def gate(self, tests=None, process=None):
        tests = tests or [Mock(returncode=0, stderr=b'Ran 1 test')]
        with patch.object(check.evidence, 'archive', return_value=(self.root, {'commit': 'fixture'})), patch.object(check.subprocess, 'run'), \
                patch.object(check.subprocess, 'check_output', return_value=b'app.py\n'), patch.object(check.boundary, 'load', return_value={}), \
                patch.object(check.boundary, 'scan'), patch.object(check, 'tests', side_effect=tests) as ran, \
                patch.object(check.subprocess, 'Popen', return_value=process or self.process()) as spawn, \
                contextlib.redirect_stdout(io.StringIO()) as printed, contextlib.redirect_stderr(io.StringIO()):
            self.spawn = spawn
            check.main(self.root, self.receipt)
        self.printed = json.loads(printed.getvalue())
        return ran, spawn

    def test_no_file_keeps_the_loop_and_receipt_keys(self):
        ran, spawn = self.gate([Mock(returncode=0, stderr=b'Ran 1 test'), Mock(returncode=1)])
        self.assertEqual(ran.call_count, 2)
        self.assertEqual(self.printed['mutations'], 1)
        spawn.assert_not_called()
        receipt = json.loads(self.receipt.read_text())
        self.assertEqual(receipt['killed'], ['answer'])
        self.assertEqual(sorted(receipt), ['boundary', 'commit', 'killed', 'ok', 'tests'])

    def test_valid_file_runs_runner_instead_of_loop(self):
        self.configure()
        ran, spawn = self.gate()
        self.assertEqual(ran.call_count, 1)
        args, kwargs = spawn.call_args
        self.assertEqual(args[0], GOOD['mutation_runner'])
        self.assertEqual((kwargs['cwd'], kwargs['env']['N5KIT_OFFLINE'], kwargs['start_new_session']), (self.root, '1', True))
        self.assertFalse(kwargs.get('shell'))
        spawn.return_value.communicate.assert_called_once_with(timeout=3600)
        receipt = json.loads(self.receipt.read_text())
        self.assertEqual((receipt['mutations'], self.printed['mutations']), ('external', 'external'))
        self.assertNotIn('killed', receipt)
        runner = receipt['mutation_runner']
        self.assertEqual((runner['argv'], runner['reason'], runner['rc']), (GOOD['mutation_runner'], 'strict runner', 0))
        self.assertEqual(runner['output'], '0 survived; 3/3 killed')
        self.assertIn('seconds', runner)

    def test_output_tail_is_4k(self):
        self.configure()
        self.gate(process=self.process(b'x' * 5000))
        self.assertEqual(len(json.loads(self.receipt.read_text())['mutation_runner']['output']), 4096)

    def test_runner_failure_and_timeout_fail_the_gate(self):
        self.configure()
        with self.assertRaisesRegex(RuntimeError, 'mutation_runner_failed'):
            self.gate(process=self.process(b'1 survived', 1))
        self.assertFalse(self.receipt.exists())
        hung = self.process()
        hung.communicate.side_effect = [subprocess.TimeoutExpired('runner', 3600), (b'', None)]
        hung.pid = 4242
        with patch.object(check.os, 'killpg') as kill, self.assertRaisesRegex(RuntimeError, 'mutation_runner_failed'):
            self.gate(process=hung)
        kill.assert_called_once_with(4242, check.signal.SIGKILL)
        self.assertFalse(self.receipt.exists())

    def test_invalid_files_fail_with_a_clear_code(self):
        bad = [{}, [], 'not json', '{', dict(GOOD, extra=1), {k: v for k, v in GOOD.items() if k != 'reason'},
               {k: v for k, v in GOOD.items() if k != 'timeout_s'}, dict(GOOD, reason=''), dict(GOOD, reason='  '), dict(GOOD, reason=3),
               dict(GOOD, mutation_runner='python3 tests/mutate.py; rm -rf x'), dict(GOOD, mutation_runner=[]),
               dict(GOOD, mutation_runner=['']), dict(GOOD, mutation_runner=['python3', 3]), dict(GOOD, timeout_s=59),
               dict(GOOD, timeout_s=7201), dict(GOOD, timeout_s=True), dict(GOOD, timeout_s='60'), dict(GOOD, timeout_s=60.0)]
        for value in bad:
            with self.subTest(value=value):
                self.configure(value)
                with self.assertRaisesRegex(RuntimeError, 'mutation_runner_invalid'):
                    self.gate()

    def test_timeout_limits_are_inclusive(self):
        for seconds in (60, 7200):
            with self.subTest(seconds=seconds):
                self.configure(dict(GOOD, timeout_s=seconds))
                self.receipt.unlink(missing_ok=True)
                _, spawn = self.gate()
                spawn.return_value.communicate.assert_called_once_with(timeout=seconds)

    def test_uniqueness_is_still_checked_with_a_runner(self):
        self.configure()
        for text in ('other', 'answer=42 answer=42'):
            with self.subTest(text=text):
                (self.root / 'app.py').write_text(text)
                with self.assertRaisesRegex(RuntimeError, 'mutation_not_unique'):
                    self.gate()
                self.spawn.assert_not_called()

    def test_runner_without_mutations_file_is_allowed(self):
        self.configure()
        (self.root / 'mutations.json').unlink()
        self.gate()
        self.assertEqual(json.loads(self.receipt.read_text())['mutations'], 'external')
