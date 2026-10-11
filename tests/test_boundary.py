import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from n5kit import boundary


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'SPEC.md').write_text('Own isolated synthetic files only.\n')
        (self.root/'SCOPE-LOG.md').write_text('Wesley #0232 approved synthetic policy.\n')
        (self.root/'tests').mkdir()
        self.policy = {'project':'synthetic', 'canon':[{'file':'SPEC.md','lines':'1-1'}],
                       'write_paths':[str(self.root/'out')], 'forbidden_paths':['/denied-project'],
                       'allowed_inputs':[], 'databases':{'allowed':[], 'forbidden_patterns':['postgresql://.*otherdb']},
                       'network_hosts':{'allowed':[]}, 'forbidden_imports':['private_core'],
                       'never_store_fields':['secret_contact'], 'trusted_sockets':[],
                       'scope_log':'SCOPE-LOG.md', 'scan_exclusions':{'tests/**':'synthetic refusal fixtures'}}
        self.save()

    def save(self):
        (self.root/'BOUNDARY.yaml').write_text(json.dumps(self.policy))

    def test_valid_policy_binding_and_refusals(self):
        record = boundary.load(self.root)
        self.assertEqual(record['policy'], self.policy)
        self.assertEqual(len(record['sha256']), 64)
        for key, value in [('project',''), ('trusted_sockets',['anything']), ('never_store_fields','x'), ('write_paths',['/']), ('network_hosts',{})]:
            with self.subTest(key=key):
                original=self.policy[key]
                self.policy[key]=value
                self.save()
                with self.assertRaises(RuntimeError): boundary.load(self.root)
                self.policy[key]=original
        self.save()
        (self.root/'BOUNDARY.yaml').write_text(json.dumps(self.policy)[:-1]+',"project":"duplicate"}')
        with self.assertRaises(RuntimeError): boundary.load(self.root)

    def test_paths_canon_scope_and_symlinks_fail_closed(self):
        for edit in [lambda: self.policy.update(unrecognized=True),
                     lambda: self.policy.update(write_paths=['/denied-project/output']),
                     lambda: self.policy.update(allowed_inputs=[{'path':'/denied-project','contract':'v1'}]),
                     lambda: self.policy.update(canon=[{'file':'SPEC.md','lines':'2-4'}]),
                     lambda: self.policy.update(scope_log='missing.md')]:
            original=json.loads(json.dumps(self.policy))
            edit()
            self.save()
            with self.assertRaises(RuntimeError): boundary.load(self.root)
            self.policy=original
        (self.root/'alias').symlink_to('/denied-project', target_is_directory=True)
        self.policy['write_paths']=[str(self.root/'alias'/'out')]
        self.save()
        with self.assertRaises(RuntimeError): boundary.load(self.root)

    def test_tree_and_diff_rules_and_explicit_fixture_exclusion(self):
        self.policy['databases']['allowed']=['postgresql://.*']
        self.save()
        rows=['import private_core as harmless\n', 'from private_core.sub import x\n',
              'path="/denied-project/data"\n', 'db="postgresql://user@host/otherdb"\n',
              'url="https://outside.invalid/data"\n', 'SECRET_CONTACT="x"\n']
        for text in rows:
            with self.subTest(text=text):
                (self.root/'app.py').write_text(text)
                with self.assertRaises(RuntimeError): boundary.scan(self.root, boundary.load(self.root))
                (self.root/'app.py').write_text('answer=42\n')
                with self.assertRaises(RuntimeError): boundary.scan(self.root, boundary.load(self.root), {'app.py':text})
        (self.root/'app.py').write_text('db="mysql://user@host/other"')
        with self.assertRaises(RuntimeError): boundary.scan(self.root, boundary.load(self.root))
        (self.root/'app.py').write_text('answer=42')
        (self.root/'tests'/'denied.py').write_text(rows[0])
        boundary.scan(self.root, boundary.load(self.root))
        self.policy['network_hosts']['allowed']=['inside.example']
        self.save()
        (self.root/'app.py').write_text('url="https://inside.example/data"\n')
        boundary.scan(self.root, boundary.load(self.root))

    def test_native_mounts_network_and_environment(self):
        (self.root/'input').mkdir()
        (self.root/'out').mkdir()
        self.policy['allowed_inputs']=[{'path':str(self.root/'input'),'contract':'synthetic-v1'}]
        self.save()
        spec={'_boundary':boundary.load(self.root)}
        args=boundary.native(spec, ['/usr/bin/true'], self.root/'out')
        for flag in ('--unshare-all','--die-with-parent','--clearenv','--new-session','--cap-drop'): self.assertIn(flag,args)
        self.assertNotIn('--share-net',args)
        self.assertIn('--ro-bind',args)
        self.assertNotIn('/denied-project',args)
        self.assertNotIn('/',args)
        self.assertEqual(args[-1],'/usr/bin/true')
        self.policy['network_hosts']['allowed']=['inside.example']
        self.policy['trusted_sockets']=['user-bus']
        self.save()
        with patch.object(boundary.Path,'exists',return_value=True):
            args=boundary.native({'_boundary':boundary.load(self.root)},['/usr/bin/true'],self.root/'out')
        self.assertIn('--share-net',args)
        self.assertIn('/run/user/'+str(boundary.os.getuid())+'/bus',args)
        record=boundary.load(self.root)
        (self.root/'input').rmdir()
        (self.root/'input').symlink_to('/denied-project')
        with self.assertRaises(RuntimeError): boundary.native({'_boundary':record},['true'],self.root/'out')

    def test_binding_denies_output_escape_and_docker_is_readonly(self):
        with patch.object(boundary.Path,'cwd',return_value=self.root):
            spec=boundary.bind({}, self.root/'out')
            self.assertEqual(spec['_boundary']['root'],str(self.root))
            with self.assertRaises(RuntimeError): boundary.bind({},self.root.parent/'escape')
        args=boundary.docker(spec['_boundary'], self.root/'output')
        self.assertIn('--read-only',args)
        self.assertEqual(args.count('--tmpfs'),2)
        with self.assertRaises(RuntimeError): boundary.native({},['true'],self.root/'out')

    def test_run_and_cleanup_use_frozen_policy(self):
        from n5kit import run
        from unittest.mock import Mock
        out = self.root/'out'
        def execute(args, **kwargs):
            self.assertIn('--setenv=N5KIT_CONFIG_SHA256='+boundary.hashlib.sha256((out/('a'*32)/'config.json').read_bytes()).hexdigest(), args)
            (out/('a'*32)/'END.json').write_text('{"ok":true}')
            return Mock(returncode=0)
        with patch.object(boundary.Path,'cwd',return_value=self.root), patch.object(run.host,'window'), patch.object(run.host,'probe'), patch.object(run.host,'inventory',return_value={}), patch.object(run.host,'scheduled'), patch.object(run.host,'book'), patch.object(run.subprocess,'run',side_effect=execute):
            path=run.submit({'mode':'native','argv':['/usr/bin/true'],'cleanup_argv':['/usr/bin/true']},out,'a'*32)
        start=json.loads((path/'START.json').read_text())
        self.assertEqual(start['boundary']['sha256'],boundary.load(self.root)['sha256'])
        self.assertEqual(start['boundary']['policy']['network_hosts']['allowed'],[])
        (path/'END.json').unlink()
        with patch.object(run.host,'window'), patch.object(run.host,'probe',return_value={}), patch.object(run.host,'inventory',return_value={}), patch.object(run.host,'scheduled'), patch.object(run.subprocess,'Popen') as spawn, patch.object(run,'native_sample',return_value={'running':False,'exit':0,'descendants':[]}):
            run.worker(path/'config.json')
            self.assertEqual(spawn.call_args.args[0][0],'bwrap')
            self.assertIn('--unshare-all',spawn.call_args.args[0])
        with patch.object(run.subprocess,'run',return_value=Mock(returncode=0)) as callback, patch.object(run.host,'release'), patch.dict(run.os.environ,{'SERVICE_RESULT':'success','N5KIT_CONFIG_SHA256':boundary.hashlib.sha256((path/'config.json').read_bytes()).hexdigest()}):
            run.cleanup(path/'config.json')
            self.assertEqual(callback.call_args.args[0][0],'bwrap')
        self.assertEqual(json.loads((path/'END.json').read_text())['boundary'],start['boundary'])

    def test_make_check_invokes_tree_and_diff_scan(self):
        from n5kit import check
        from unittest.mock import Mock
        (self.root/'app.py').write_text('answer=42')
        (self.root/'mutations.json').write_text('[]')
        with patch.object(check.evidence,'archive',return_value=(self.root,{'commit':'fixture'})), patch.object(check.subprocess,'run'), patch.object(check.subprocess,'check_output',return_value=b'app.py\n'), patch.object(check,'tests',return_value=Mock(returncode=0,stderr=b'Ran 1 test')), patch.object(boundary,'scan',wraps=boundary.scan) as scanned:
            check.main(self.root,self.root/'receipt.json')
        self.assertEqual(scanned.call_count,1)
        self.assertEqual(scanned.call_args.args[2],{'app.py':'answer=42'})

    def test_cleanup_rejects_tampered_or_unbound_control(self):
        from n5kit import run
        import hashlib
        config=self.root/'config.json'
        original={'mode':'native','token':'a'*32,'cleanup_argv':['true'],'_boundary':boundary.load(self.root)}
        config.write_text(json.dumps(original))
        digest=hashlib.sha256(config.read_bytes()).hexdigest()
        config.write_text(json.dumps(dict(original,cleanup_argv=['changed'])))
        for expected in (digest, ''):
            with patch.dict(run.os.environ, {'N5KIT_CONFIG_SHA256':expected}), patch.object(run.subprocess,'run',return_value=Mock(returncode=0)) as callback, patch.object(run.host,'release') as release:
                with self.assertRaisesRegex(RuntimeError, 'config_changed'): run.cleanup(config)
                callback.assert_not_called()
                release.assert_not_called()
        record=boundary.load(self.root)
        for args in (boundary.native({'_boundary':record},['true'],self.root/'output'), boundary.docker(record, self.root/'output')):
            for name in ('config.json','START.json','timers.json'):
                self.assertTrue(any(str(self.root/name) in v for v in args))
