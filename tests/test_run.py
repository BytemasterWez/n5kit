import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from n5kit import run

class RunTests(unittest.TestCase):
    def test_transient_service_owns_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            token = 'a'*32
            def execute(args, **kw):
                if args[0]=='systemd-run':
                    self.assertNotIn('--scope', args)
                    for value in ['RuntimeMaxSec=60', 'MemoryMax=256M', 'TasksMax=64', 'KillMode=control-group']:
                        self.assertIn('--property='+value, args)
                    self.assertTrue(any('ExecStopPost=' in v for v in args))
                    (Path(temp)/token/'END.json').write_text('{"ok":true}')
                return type('Result', (), {'returncode':0})()
            with patch.object(run.host, 'window'), patch.object(run.host, 'probe'), patch.object(run.host, 'inventory', return_value={}), patch.object(run.host, 'book') as book, patch.object(run.subprocess, 'run', side_effect=execute):
                out = run.submit({'seconds':60}, temp, token)
            self.assertTrue((out/'START.json').exists())
            book.assert_called_once_with(token,150)

    def test_sample_gap_and_exit_failure(self):
        for gap, status in [(31,0),(5,1)]:
            with tempfile.TemporaryDirectory() as temp:
                path=Path(temp)/'config.json'
                path.write_text(json.dumps(dict(token='a'*32,image='image',argv=['true'])))
                with patch.object(run.host, 'probe', return_value={}), patch.object(run.docker, 'create'), patch.object(run.docker, 'container'), patch.object(run.docker, 'sample', return_value={'running':False,'exit':status}), patch.object(run.time, 'monotonic', side_effect=[0,gap]):
                    with self.assertRaises(RuntimeError): run.worker(path)

    def test_runtime_cap(self):
        for seconds in (0,1801):
            with self.assertRaisesRegex(RuntimeError,'runtime_limit'):
                run.submit({'seconds':seconds}, '/tmp/unused-n5kit')

    def test_cleanup_releases_after_removal(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'config.json'
            path.write_text(json.dumps(dict(token='a'*32,image='image',uid=1000,gid=1000)))
            with patch.object(run.docker, 'cleanup', return_value=[]) as cleanup, patch.object(run.host, 'release') as release:
                run.cleanup(path)
                cleanup.assert_called_once()
                release.assert_called_once_with('a'*32)
            self.assertFalse(json.loads((path.parent/'END.json').read_text())['ok'])

    def test_cleanup_failure_keeps_reservation(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'config.json'
            path.write_text(json.dumps(dict(token='a'*32,image='image',uid=1000,gid=1000)))
            with patch.object(run.docker, 'cleanup', side_effect=RuntimeError), patch.object(run.host, 'release') as release:
                with self.assertRaises(RuntimeError): run.cleanup(path)
                release.assert_not_called()
            self.assertTrue((path.parent/'cleanup-failed.json').exists())

    def test_injected_memory_uses_real_guard_path(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'config.json'
            path.write_text(json.dumps(dict(token='a'*32,image='image',argv=['true'],rehearsal_memory_after=0)))
            with patch.object(run.host, 'metrics', return_value={'memory':30,'docker':300,'work':150,'load':0}), patch.object(run.docker, 'create', return_value='owned'), patch.object(run.docker, 'container', return_value='owned'), patch.object(run.docker, 'sample') as sample:
                with self.assertRaisesRegex(RuntimeError, 'host_guard_failed'): run.worker(path)
                sample.assert_not_called()

if __name__ == '__main__': unittest.main()
