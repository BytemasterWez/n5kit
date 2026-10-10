import json
from pathlib import Path
import unittest
from unittest.mock import patch
from n5kit import docker

TOKEN = 'a'*32
IMAGE = 'sha256:' + 'b'*64

class DockerTests(unittest.TestCase):
    def test_wrong_label_refused(self):
        item = {'Config': {'Labels': {'n5kit.token': 'b'*32}}}
        with patch.object(docker, 'call', return_value=json.dumps([item])):
            with self.assertRaises(RuntimeError): docker.inspect('container', 'id', TOKEN)
        item['Config']['Labels']['n5kit.token'] = TOKEN
        with patch.object(docker, 'call', return_value=json.dumps([item])):
            self.assertEqual(docker.inspect('container', 'id', TOKEN), item)

    def test_limits_are_on_container(self):
        with patch.object(docker, 'call', return_value='id') as call, patch.object(docker, 'inspect'):
            docker.container(TOKEN, IMAGE, '/owned/output', ['sleep', '2'])
        args = call.call_args_list[0].args
        for key, value in [('--memory', '536870912'), ('--memory-swap', '536870912'), ('--pids-limit', '64'), ('--cpus', '1'), ('--network', 'none')]:
            self.assertEqual(args[args.index(key)+1], value)
        self.assertIn('n5kit.token='+TOKEN, args)

    def test_oom_and_missing_counter_refused(self):
        item = {'State': {'Running': True, 'OOMKilled': False, 'Pid': 123, 'ExitCode': 0}}
        for events in ('oom 1\noom_kill 0', 'oom 0\noom_kill 1', 'oom_kill 0'):
            def read(path):
                return '0::/fake' if str(path).startswith('/proc/') else {'memory.events':events,'memory.peak':'1000000','pids.peak':'3'}[path.name]
            with patch.object(docker, 'inspect', return_value=item), patch.object(Path, 'read_text', read):
                with self.assertRaises((RuntimeError, KeyError)): docker.sample('id', TOKEN)
        item['State'].update(OOMKilled=True, Running=False, ExitCode=137)
        with patch.object(docker, 'inspect', return_value=item):
            with self.assertRaises(RuntimeError): docker.sample('id', TOKEN)

    def test_cleanup_keeps_trying_after_chown_failure(self):
        resources = {'container': ['cid'], 'network': ['nid'], 'volume': ['vid']}
        def call(*args, **kw):
            if args[1] == 'rm': resources[args[0]].remove(args[-1])
            return ''
        with patch.object(Path, 'iterdir', return_value=iter([Path('root.txt')])), patch.object(docker, 'owned', side_effect=lambda k,t: list(resources[k])), patch.object(docker, 'call', side_effect=call), patch.object(docker, 'container', side_effect=RuntimeError('chown')):
            with self.assertRaises(RuntimeError): docker.cleanup(TOKEN, IMAGE, '/owned/output')
        self.assertEqual(resources, {'container': [], 'network': [], 'volume': []})

    def test_token_and_image_refusals(self):
        with self.assertRaises(RuntimeError): docker.owned('container', 'x')
        with self.assertRaises(RuntimeError): docker.container(TOKEN, 'latest', '/owned', ['true'])

if __name__ == '__main__': unittest.main()
