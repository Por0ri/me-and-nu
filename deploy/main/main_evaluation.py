"""Install or inspect the fixed Main evaluation timer; no arbitrary commands.

dry-run prints the exact unit content without changing the host. enable installs
the checked-in units and enables the timer; run executes one job immediately.
disable stops the timer only and preserves collected cases and published scores.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

import main_publish_evaluation as publisher
import main_runtime as runtime

ROOT = publisher.ROOT
UNIT_DIRECTORY = Path('/etc/systemd/system')
SERVICE = 'me-nu-main-evaluation.service'
TIMER = 'me-nu-main-evaluation.timer'
UNITS = (SERVICE, TIMER)
UNIT_TEMPLATE_ROOT = '/home/ubuntu/apps/me-nu-main-20261118'


def units():
    result = {}
    for name in UNITS:
        path = ROOT / 'deploy/main/systemd' / name
        publisher.no_symlinks(path)
        data = path.read_bytes()
        if len(data) > 8192 or b'\0' in data:
            raise publisher.PublishError('Unexpected evaluation unit contents.')
        # The checked-in deployment root is an example. Render the actual
        # reviewed checkout, which require_host checks before installation.
        data = data.replace(UNIT_TEMPLATE_ROOT.encode(), str(ROOT).encode())
        result[name] = data
    return result


def install_units(contents):
    publisher.no_symlinks(UNIT_DIRECTORY)
    if not UNIT_DIRECTORY.is_dir():
        raise publisher.PublishError('systemd unit directory is unavailable.')
    for name, data in contents.items():
        if name not in UNITS:
            raise publisher.PublishError('Only fixed evaluation units can be installed.')
        target = UNIT_DIRECTORY / name
        publisher.no_symlinks(target)
        if target.exists() and not stat.S_ISREG(target.stat().st_mode):
            raise publisher.PublishError('An evaluation unit is not a regular file.')
        descriptor, temporary = tempfile.mkstemp(prefix='.main-evaluation-', dir=UNIT_DIRECTORY)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o644)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    publisher.sync_directory(UNIT_DIRECTORY)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('dry-run', 'status', 'enable', 'disable', 'run'))
    args = parser.parse_args(argv)
    try:
        if args.action == 'dry-run':
            for name, content in units().items():
                print(f'# {UNIT_DIRECTORY / name}\n' + content.decode())
            return 0
        runtime.require_host(__file__)
        if args.action == 'status':
            print(publisher.run(['systemctl', 'show', TIMER, SERVICE, '--property=Id,LoadState,ActiveState,SubState,Result,LastTriggerUSec,NextElapseUSecRealtime']))
            return 0
        if os.geteuid() != 0:
            raise publisher.PublishError('Use sudo for evaluation timer changes.')
        if args.action == 'enable':
            from main_evaluation_pipeline import JOB_LOCK, LEGACY_JOB_LOCK, lock
            with lock(JOB_LOCK), lock(LEGACY_JOB_LOCK), lock(publisher.LOCK):
                runtime.require_legacy_stopped('evaluation')
                publisher.runtime_identity(ROOT / 'deploy/main/admin-evaluation', 'service')
                install_units(units())
                publisher.run(['systemctl', 'daemon-reload'])
                publisher.run(['systemctl', 'enable', '--now', TIMER])
            print('Main evaluation timer enabled; completed observations are required for scores.')
        elif args.action == 'disable':
            publisher.run(['systemctl', 'disable', '--now', TIMER])
            print('Main evaluation timer disabled; an active job may finish. Data and results are preserved.')
        else:
            result = subprocess.run([sys.executable, str(ROOT / 'deploy/main/main_evaluation_pipeline.py')], check=False)
            return result.returncode
        return 0
    except (publisher.PublishError, runtime.RuntimeGuardError, OSError, ValueError):
        print('Main evaluation action failed; check deployment and systemd state.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
