"""Reviewed local policy, literal/AST gate and kernel filesystem isolation."""
import ast
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from . import require

KEYS = {'project', 'canon', 'write_paths', 'forbidden_paths', 'allowed_inputs', 'databases', 'network_hosts', 'forbidden_imports', 'never_store_fields', 'trusted_sockets', 'scope_log', 'scan_exclusions'}


def unique(pairs):
    require(len(dict(pairs)) == len(pairs), 'boundary_duplicate')
    return dict(pairs)


def load(root):
    root = Path(root).resolve()
    try:
        raw = local(root, 'BOUNDARY.yaml').read_bytes()
        p = json.loads(raw, object_pairs_hook=unique)
        require(set(p) == KEYS and isinstance(p['project'], str) and bool(p['project'].strip()), 'boundary_schema')
        for key in ('write_paths', 'forbidden_paths', 'forbidden_imports', 'never_store_fields', 'trusted_sockets'):
            require(isinstance(p[key], list) and all(isinstance(v, str) and v.strip() for v in p[key]), 'boundary_list')
        require(set(p['databases']) == {'allowed', 'forbidden_patterns'} and set(p['network_hosts']) == {'allowed'}, 'boundary_targets')
        for values in (*p['databases'].values(), p['network_hosts']['allowed']):
            require(isinstance(values, list) and all(isinstance(v, str) and v.strip() for v in values), 'boundary_targets')
        require(set(p['trusted_sockets']) <= {'docker', 'user-bus'}, 'boundary_socket')
        require(isinstance(p['scan_exclusions'], dict) and all(isinstance(k, str) and isinstance(v, str) and k and v for k, v in p['scan_exclusions'].items()), 'boundary_exclusions')
        for item in p['canon']:
            lines = local(root, item['file']).read_text().splitlines()
            lo, hi = map(int, item['lines'].split('-'))
            require(0 < lo <= hi <= len(lines), 'boundary_canon')
        require(p['canon'] and local(root, p['scope_log']).read_text().strip(), 'boundary_scope_log')
        inputs = [item['path'] for item in p['allowed_inputs'] if item['contract'].strip()]
        require(len(inputs) == len(p['allowed_inputs']), 'boundary_input_contract')
        for value in p['write_paths'] + inputs + p['forbidden_paths']:
            path = Path(value)
            require(path.is_absolute() and str(path.resolve()) == value and len(path.parts) >= 2, 'boundary_path')
        require(not any(v in ('/', '/home', str(Path.home()), '/home/wez/Work', '/home/wez/Projects') for v in inputs), 'boundary_broad_input')
        for value in p['write_paths'] + inputs + [str(root)]:
            require(not any(overlap(value, denied) for denied in p['forbidden_paths']), 'boundary_forbidden_path')
        require(all(Path(v).is_relative_to(root) for v in p['write_paths']), 'boundary_write_root')
        require(not any(overlap(a, b) for a in inputs for b in p['write_paths']), 'boundary_input_write')
        return {'policy': p, 'root': str(root), 'sha256': hashlib.sha256(raw).hexdigest()}
    except Exception as error:
        raise RuntimeError('boundary_invalid') from error


def local(root, name):
    path = root/name
    require(path.resolve().is_relative_to(root) and str(path.resolve()) == str(path), 'boundary_local_file')
    return path


def overlap(a, b):
    return Path(a).is_relative_to(b) or Path(b).is_relative_to(a)


def scan(root, record, diff=None):
    p = record['policy']
    require(not any(f.is_symlink() for f in Path(root).rglob('*')), 'boundary_tree_symlink')
    exempt = {'BOUNDARY.yaml', p['scope_log'], *(v['file'] for v in p['canon'])}
    for name, text in [(str(f.relative_to(root)), f.read_text(errors='replace')) for f in Path(root).rglob('*') if f.is_file() and '.git' not in f.parts] + list((diff or {}).items()):
        if name in exempt or any(fnmatch.fnmatch(name, glob) for glob in p['scan_exclusions']): continue
        require(not any(v.lower() in text.lower() for v in p['never_store_fields']), 'boundary_field')
        require(not any(v in text for v in p['forbidden_paths']), 'boundary_path_literal')
        require(not any(re.search(v, text) for v in p['databases']['forbidden_patterns']), 'boundary_database')
        connections = re.findall(r'(?:postgres(?:ql)?|mysql|sqlite)://[^\s\"\']+', text)
        require(all(any(re.fullmatch(v, c) for v in p['databases']['allowed']) for c in connections), 'boundary_database_allowlist')
        hosts = re.findall(r'(?<![\w.])(?:[A-Za-z0-9-]+\.)+(?:com|org|gov|net|io|example)\b|\b(?:\d{1,3}\.){3}\d{1,3}\b|\blocalhost\b', text)
        hosts += [urlsplit(v).hostname for v in re.findall(r'https?://[^\s\"\']+', text)]
        require(set(hosts) <= set(p['network_hosts']['allowed']), 'boundary_host')
        if name.endswith('.py'):
            for node in ast.walk(ast.parse(text)):
                names = [v.name for v in node.names] if isinstance(node, ast.Import) else [node.module or ''] if isinstance(node, ast.ImportFrom) else []
                require(not any(n == v or n.startswith(v+'.') for n in names for v in p['forbidden_imports']), 'boundary_import')


def bind(spec, output):
    record = load(Path.cwd())
    require(any(Path(output).resolve().is_relative_to(v) for v in record['policy']['write_paths']), 'boundary_output')
    return dict(spec, _boundary=record)


def native(spec, argv, output):
    require('_boundary' in spec, 'boundary_missing')
    r, args = spec['_boundary'], ['bwrap', '--unshare-all', '--die-with-parent', '--new-session', '--clearenv', '--cap-drop', 'ALL']
    p = r['policy']
    if p['network_hosts']['allowed']: args += ['--share-net']
    require(all(str(Path(v['path']).resolve()) == v['path'] and Path(v['path']).exists() for v in p['allowed_inputs']), 'boundary_input_alias')
    for path in ['/usr', '/bin', '/lib', '/lib64', '/etc/ld.so.cache', '/etc/resolv.conf', '/etc/ssl', r['root']] + [v['path'] for v in p['allowed_inputs']]:
        if Path(path).exists(): args += ['--ro-bind', path, path]
    args += ['--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--tmpfs', '/run']
    for path in p['write_paths']:
        require(not Path(path).is_symlink() and str(Path(path).resolve()) == path, 'boundary_write_alias')
        if Path(path).exists(): args += ['--bind', path, path]
    control = Path(output).parent if Path(output).name == 'output' else Path(output)
    for name in ('config.json', 'START.json', 'timers.json'):
        args += ['--ro-bind', str(control/name), str(control/name)]
    sockets = {'docker':'/run/docker.sock', 'user-bus':f'/run/user/{os.getuid()}/bus'}
    for name in p['trusted_sockets']:
        path = sockets[name]
        require(Path(path).exists(), 'boundary_socket_missing')
        args += ['--ro-bind', path, path]
        args += ['--setenv', 'DOCKER_HOST' if name == 'docker' else 'DBUS_SESSION_BUS_ADDRESS', ('unix://' if name == 'docker' else 'unix:path=')+path]
    argv = [str(Path(argv[0]).resolve()) if Path(argv[0]).is_absolute() else argv[0], *argv[1:]]
    return args + ['--setenv', 'PATH', '/usr/bin:/bin', '--setenv', 'HOME', '/nonexistent', '--chdir', str(output), '--', *argv]


def docker(record, output):
    p = record['policy']
    require(not p['trusted_sockets'], 'boundary_docker_socket_unsupported')
    args = ['--read-only', '--cap-drop=ALL', '--cap-add=DAC_OVERRIDE', '--tmpfs', '/tmp:rw,noexec,nosuid', '--tmpfs', '/run:rw,noexec,nosuid']
    for path in [record['root']] + [v['path'] for v in p['allowed_inputs']]:
        args += ['--mount', f'type=bind,src={path},dst={path},readonly']
    for name in ('config.json', 'START.json', 'timers.json'):
        path = str(Path(output).parent/name)
        args += ['--mount', f'type=bind,src={path},dst={path},readonly']
    return args
