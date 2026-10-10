import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
from n5kit import run

class RunTests(unittest.TestCase):
    def test_transient_service_owns_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            token = 'a'*32
            def execute(args, **kw):
                if args[0]=='systemd-run':
                    self.assertNotIn('--scope', args)
                    self.assertIn('--setenv=N5KIT_REAL=1', args)
                    self.assertIn('--setenv=N5KIT_OFFLINE=0', args)
                    for value in ['RuntimeMaxSec=60', 'MemoryMax=256M', 'TasksMax=64', 'KillMode=control-group']:
                        self.assertIn('--property='+value, args)
                    self.assertTrue(any('ExecStopPost=' in v for v in args))
                    (Path(temp)/token/'END.json').write_text('{"ok":true}')
                return type('Result', (), {'returncode':0})()
            with patch.object(run.host, 'window'), patch.object(run.host, 'probe'), patch.object(run.host, 'inventory', return_value={}), patch.object(run.host, 'scheduled'), patch.object(run.host, 'book') as book, patch.object(run.subprocess, 'run', side_effect=execute):
                out = run.submit({'seconds':60}, temp, token)
            self.assertTrue((out/'START.json').exists())
            book.assert_called_once_with(token,150)

    def test_sample_gap_and_exit_failure(self):
        for gap, status in [(31,0),(5,1)]:
            with tempfile.TemporaryDirectory() as temp:
                path=Path(temp)/'config.json'
                path.write_text(json.dumps(dict(token='a'*32,image='image',argv=['true'])))
                with patch.object(run.host, 'window'), patch.object(run.host, 'inventory'), patch.object(run.host, 'scheduled'), patch.object(run.host, 'probe', return_value={}), patch.object(run.docker, 'create'), patch.object(run.docker, 'container'), patch.object(run.docker, 'sample', return_value={'running':False,'exit':status}), patch.object(run.time, 'monotonic', side_effect=[0,gap]):
                    with self.assertRaises(RuntimeError): run.worker(path)

    def test_runtime_cap(self):
        for seconds in (0,1801):
            with patch.object(run.host, 'window', side_effect=AssertionError('admission reached')), self.assertRaisesRegex(RuntimeError,'runtime_limit'):
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
            with patch.object(run.host, 'window'), patch.object(run.host, 'inventory'), patch.object(run.host, 'scheduled'), patch.object(run.host, 'metrics', return_value={'memory':30,'docker':300,'work':150,'load':0}), patch.object(run.docker, 'create', return_value='owned'), patch.object(run.docker, 'container', return_value='owned'), patch.object(run.docker, 'sample') as sample:
                with self.assertRaisesRegex(RuntimeError, 'host_guard_failed'): run.worker(path)
                sample.assert_not_called()

    def test_native_validation_and_direct_argv(self):
        for spec in ({'mode':'unknown'}, {'mode':'native','argv':'true'}, {'mode':'native','argv':[]}, {'mode':'native','argv':[1]}):
            with patch.object(run.host, 'window', side_effect=AssertionError('admission reached')), self.assertRaisesRegex(RuntimeError, 'invalid_job'):
                run.submit(spec, '/tmp/unused-n5kit')
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'config.json'
            argv=['/usr/bin/python','-c','literal; $(not a shell)']
            path.write_text(json.dumps(dict(mode='native',token='a'*32,argv=argv)))
            with patch.object(run.host, 'window'), patch.object(run.host, 'inventory'), patch.object(run.host, 'scheduled'), patch.object(run.host, 'probe', return_value={}), patch.object(run.docker, 'create', side_effect=AssertionError('Docker called')), patch.object(run.subprocess, 'Popen') as spawn, patch.object(run, 'native_sample', return_value={'running':False,'exit':0,'descendants':[99]}):
                run.worker(path)
                self.assertEqual(spawn.call_args.args[0], argv)
                self.assertFalse(spawn.call_args.kwargs['shell'])
                self.assertEqual(spawn.call_args.kwargs['cwd'], path.parent/'output')
            self.assertEqual(json.loads((path.parent/'job.json').read_text())['orphans_killed'],1)
            self.assertGreaterEqual(json.loads((path.parent/'worker-baseline.json').read_text())['worker_tasks'],1)
            with patch.object(run.docker, 'cleanup', side_effect=AssertionError('Docker called')), patch.object(run.host, 'release') as release, patch.dict(run.os.environ, {'SERVICE_RESULT':'success'}):
                run.cleanup(path)
                release.assert_called_once_with('a'*32)
            end=json.loads((path.parent/'END.json').read_text())
            self.assertTrue(end['ok'])
            self.assertEqual(end['orphans_killed'],1)
            self.assertIn('external services excluded',end['limits_scope'])

    def test_native_limits(self):
        self.assertEqual(run.native_limits({'memory':8*2**30,'pids':256,'seconds':14400}), (8*2**30,256,14400))
        for key, top in (('memory',8*2**30), ('pids',256), ('seconds',14400)):
            for value in (True, 0, -1, '10', 1.5, top+1):
                with patch.object(run.host, 'window', side_effect=AssertionError('admission reached')), self.assertRaisesRegex(RuntimeError,'native_limits'):
                    run.submit({'mode':'native','argv':['true'],key:value}, '/tmp/unused-n5kit')

    def test_native_cgroup_oom_and_exit(self):
        files={'cgroup':'0::/n5kit-'+('a'*32)+'.service', 'memory.events':'oom 0\noom_kill 0', 'memory.peak':'1024','pids.current':'2','pids.peak':'7','cgroup.procs':str(run.os.getpid())+'\n99'}
        with patch.object(Path, 'read_text', lambda p: files[p.name]):
            result=run.native_sample(Mock(poll=lambda:7), 'a'*32)
            self.assertEqual((result['running'],result['exit'],result['descendants'],result['pids'],result['pids_current']), (False,7,[99],7,2))
            for events in ('oom 1\noom_kill 0', 'oom 0\noom_kill 1'):
                files['memory.events']=events
                with self.assertRaisesRegex(RuntimeError,'native_oom'): run.native_sample(Mock(poll=lambda:None),'a'*32)
            files['cgroup']='0::/unbounded.scope'
            with self.assertRaisesRegex(RuntimeError,'native_outside_unit'): run.native_sample(Mock(),'a'*32)

if __name__ == '__main__': unittest.main()
