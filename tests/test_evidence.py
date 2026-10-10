import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from n5kit import evidence, check

class EvidenceTests(unittest.TestCase):
    def test_tamper_symlink_and_private_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evidence.receipt(root/'receipt.json', {'ok': True})
            self.assertEqual((root/'receipt.json').stat().st_mode & 0o777, 0o600)
            hashes = evidence.manifest(root)
            evidence.verify(root, hashes)
            (root/'receipt.json').write_text('{}')
            with self.assertRaises(RuntimeError): evidence.verify(root, hashes)
            (root/'link').symlink_to(root/'receipt.json')
            with self.assertRaises(RuntimeError): evidence.manifest(root)

    def test_archive_and_dirty_refusal(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, target = Path(temp)/'repo', Path(temp)/'target'
            repo.mkdir()
            target.mkdir()
            def git(*args):
                subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)
            git('init')
            (repo/'a').write_text('known\n')
            git('add', 'a')
            git('-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'fixture')
            tree, record = evidence.archive(repo, target)
            self.assertEqual((tree/'a').read_text(), 'known\n')
            self.assertFalse((tree/'.git').exists())
            self.assertEqual(len(record['archive_sha256']), 64)
            (repo/'a').write_text('dirty')
            with self.assertRaises(RuntimeError): evidence.archive(repo, target)

    def test_secret_scan(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'safe').write_text(json.dumps({'ok': True}))
            check.scan(root)
            (root/'bad').write_text('ghp_' + 'a'*30)
            with self.assertRaises(RuntimeError): check.scan(root)

if __name__ == '__main__': unittest.main()
