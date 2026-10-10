import datetime as dt
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from n5kit import check, docker, host

class ReviewTests(unittest.TestCase):
    def test_drifting_timer_and_unknown_deadline(self):
        now = dt.datetime(2026,10,10,20,6,tzinfo=dt.timezone.utc)
        host.window(now,120)
        row = 'Sat 2026-10-10 20:07:19 UTC 11min Sat 2026-10-10 19:07:19 UTC 48min ago snapper-cleanup.timer snapper-cleanup.service'
        timers = {'system':{'timers':row,'active':{'snapper-cleanup.service':'inactive'}}, 'user':{'timers':'','active':{}}}
        with self.assertRaisesRegex(RuntimeError,'conflicting_timer_due'): host.scheduled(timers,now,120)
        host.scheduled(timers,now,78)
        with self.assertRaises(RuntimeError): host.scheduled(timers,now,79)
        timers['system']['timers'] = '- - - snapper-cleanup.timer snapper-cleanup.service'
        with self.assertRaisesRegex(RuntimeError,'unknown_timer_deadline'): host.scheduled(timers,now,1)
        timers['system']['timers'] = row
        timers['system']['active']['snapper-cleanup.service'] = 'active'
        with self.assertRaisesRegex(RuntimeError,'conflicting_job_active'): host.scheduled(timers,now,1)

    def test_exit_race_rechecks_terminal_state(self):
        running = {'State':{'Running':True,'OOMKilled':False,'ExitCode':0,'Pid':999}}
        stopped = {'State':{'Running':False,'OOMKilled':False,'ExitCode':0}}
        with patch.object(docker,'inspect',side_effect=[running,stopped]), patch.object(Path,'read_text',side_effect=FileNotFoundError):
            self.assertEqual(docker.sample('owned','a'*32),{'running':False,'exit':0})
        for terminal in (running, {'State':{'Running':False,'OOMKilled':True,'ExitCode':137}}):
            with patch.object(docker,'inspect',side_effect=[running,terminal]), patch.object(Path,'read_text',side_effect=PermissionError):
                with self.assertRaises(RuntimeError): docker.sample('owned','a'*32)

    def test_clean_self_archive_cannot_import_working_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            tree=root/'archive'
            (tree/'n5kit').mkdir(parents=True)
            (tree/'tests').mkdir()
            (root/'n5kit_untracked_probe_228.py').write_text('secret_fixture = True\n')
            (tree/'tests/test_import.py').write_text("import importlib,unittest\nclass T(unittest.TestCase):\n def test_no_leak(self):\n  with self.assertRaises(ModuleNotFoundError): importlib.import_module('n5kit_untracked_probe_228')\n")
            with patch.object(check,'__file__',str(root/'n5kit/check.py')):
                result=check.tests(tree)
            self.assertEqual(result.returncode,0,result.stderr.decode())

if __name__ == '__main__': unittest.main()
