"""Private receipts and clean source archives; never archive working-tree dirt."""
import hashlib
import json
import os
import subprocess
import tarfile
from . import require


def receipt(path, value):
    with open(path, 'x', opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def manifest(root):
    result = {}
    for path in sorted(root.rglob('*')):
        require(not path.is_symlink(), 'evidence_symlink')
        if path.is_file():
            with path.open('rb') as stream:
                result[str(path.relative_to(root))] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return result


def archive(repo, destination):
    require(not subprocess.check_output(['git', '-C', str(repo), 'status', '--porcelain']).strip(), 'dirty_source')
    commit = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD']).decode().strip()
    data = subprocess.check_output(['git', '-C', str(repo), 'archive', '--format=tar', commit])
    tar = destination / 'source.tar'
    tar.write_bytes(data)
    tree = destination / 'tree'
    tree.mkdir()
    with tarfile.open(tar) as stream:
        stream.extractall(tree, filter='data')
    return tree, {'commit': commit, 'archive_sha256': hashlib.sha256(data).hexdigest()}


def verify(root, expected):
    require(manifest(root) == expected, 'evidence_changed')
