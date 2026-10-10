"""Real-host acceptance; synthetic output only. Not part of offline unit discovery."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid
from n5kit import docker, evidence, host, require


def wait_for(predicate, seconds=100):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.2)
    raise RuntimeError('acceptance_wait_expired')


def execute(case, image, root):
    root.mkdir(parents=True)
    token = uuid.uuid4().hex
    receipt = {'case': case, 'at': dt.datetime.now().astimezone().isoformat(), 'token': token}
    native = case.startswith('native-')
    case = case.removeprefix('native-')
    if case == 'snapshot':
        require(not 5 <= dt.datetime.now().astimezone().minute < 52, 'not_actual_snapshot_window')
        spec = root / 'spec.json'
        evidence.receipt(spec, {'image': image, 'argv': ['true']})
        process = subprocess.run([sys.executable, '-m', 'n5kit.run', 'start', str(spec), str(root/'runs')], capture_output=True)
        require(process.returncode != 0 and not (root/'runs').exists(), 'snapshot_not_refused')
    elif case == 'booked':
        host.window(dt.datetime.now().astimezone(), 180)
        host.book(token, 180)
        try:
            from n5kit.run import submit
            try:
                submit({'image': image, 'argv': ['true']}, root/'runs')
            except RuntimeError as error:
                require(str(error) == 'host_booked', 'wrong_refusal')
            else:
                raise RuntimeError('booked_not_refused')
        finally:
            host.release(token)
    else:
        duration = 6 if case == 'pass' else 90
        code = f"import os,time; p='/output/root.txt'; open(p,'w').write('synthetic'); os.chmod(p,0o600); time.sleep({duration})"
        spec = {'image': image, 'argv': ['python', '-c', code], 'seconds': 120}
        if native:
            code = f"import os,time; from pathlib import Path; Path('root.txt').write_text('synthetic'); child=os.fork(); Path('child' if child==0 else 'parent').write_text(str(os.getpid())); time.sleep(90 if child==0 else {duration})"
            spec = {'mode':'native', 'argv':[sys.executable,'-c',code], 'seconds':120}
        if case == 'memory':
            spec['rehearsal_memory_after'] = 2
        path = root/'spec.json'
        evidence.receipt(path, spec)
        with (root/'launcher.log').open('w') as log:
            process = subprocess.Popen([sys.executable, '-m', 'n5kit.run', 'start', str(path), str(root/'runs')], stdout=log, stderr=log, start_new_session=True)
            wait_for(lambda: bool(list((root/'runs').glob('*/samples.jsonl'))))
            out = next((root/'runs').glob('*/samples.jsonl')).parent
            token = out.name
            wait_for(lambda: (out/'output/root.txt').exists())
            require((out/'output/root.txt').stat().st_uid == (1000 if native else 0), 'wrong_initial_owner')
            receipt['initial_output_uid'] = 1000 if native else 0
            if native: wait_for(lambda: (out/'output/child').exists() and (out/'output/parent').exists())
            if case in ('term', 'kill'):
                if case == 'kill':
                    os.killpg(process.pid, signal.SIGKILL)
                subprocess.run(['systemctl', '--user', 'kill', '--kill-whom=main', '--signal='+('SIGKILL' if case=='kill' else 'SIGTERM'), 'n5kit-'+token], check=True)
            wait_for(lambda: (out/'END.json').exists())
            process.wait(timeout=20)
        end = json.loads((out/'END.json').read_text())
        require(end['ok'] == (case == 'pass'), 'unexpected_job_result')
        require((out/'output/root.txt').stat().st_uid == os.getuid() == 1000, 'wrong_output_owner')
        require((out/'output/root.txt').stat().st_mode & 0o777 == 0o600, 'private_mode_changed')
        if native:
            for name in ('parent', 'child'):
                pid = int((out/'output'/name).read_text())
                require(not Path(f'/proc/{pid}').exists(), 'native_descendant_leak')
            require(end['orphans_killed'] == 1 if case == 'pass' else end['service_result'] == 'signal', 'native_cleanup_record')
            require('external services excluded' in end['limits_scope'], 'native_scope_missing')
        for kind in (() if native else ('container', 'network', 'volume')):
            require(not docker.owned(kind, token), 'acceptance_leak')
        require(token not in host.RESERVATIONS.read_text(), 'reservation_leak')
        if case == 'memory':
            require(json.loads((out/'failure.json').read_text()) == {'error':'host_guard_failed','cause':'host_memory'}, 'wrong_fault')
        samples = [json.loads(line) for line in (out/'samples.jsonl').read_text().splitlines()]
        key = 'native' if native else 'container'
        peaks = [row[key] for row in samples if row[key]['running']]
        require(peaks and max(row['gap'] for row in samples) <= 30, 'sample_coverage')
        if case == 'pass':
            require(max(row['peak'] for row in peaks) < (128 if native else 256)*2**20 and max(row['pids'] for row in peaks) < 32, 'normal_resource_margin')
            require((dt.datetime.fromisoformat(end['at']) - dt.datetime.fromisoformat(receipt['at'])).total_seconds() < 60, 'normal_time_margin')
        receipt.update(token=token, unit_end=end, launcher_returncode=process.returncode, output_uid=1000)
    receipt['ok'] = True
    evidence.receipt(root/'acceptance.json', receipt)
    print(json.dumps(receipt))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', choices=['pass', 'term', 'kill', 'snapshot', 'booked', 'memory', 'native-pass', 'native-kill'], required=True)
    parser.add_argument('--image', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    execute(args.case, args.image, args.out)
