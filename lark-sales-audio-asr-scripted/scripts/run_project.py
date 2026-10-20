#!/usr/bin/env python3
"""Portable ASR entrypoint with optional private credentials and job-field readback."""
from contextlib import contextmanager
import argparse
import json
import os
from pathlib import Path
import sys

import job_fields
import pipeline


@contextmanager
def execution_lock(path):
    """Serialize mutations of one run/config; the OS releases locks after a crash."""
    with Path(path).open('a+b') as handle:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0, 2)
            if not handle.tell():
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--config', type=Path, default=pipeline.CONFIG)
    parser.add_argument('--credentials-file', type=Path)
    options, remaining = parser.parse_known_args(sys.argv[1:] if argv is None else argv)
    if '--help' in remaining or '-h' in remaining:
        print('Project wrapper options: --config PATH, --credentials-file PATH, --sync-run RUN_DIR')
        sys.argv = [sys.argv[0], *remaining]
        return pipeline.main()
    config_path = options.config.expanduser().resolve()
    pipeline.CONFIG = config_path
    if options.credentials_file:
        values = json.loads(options.credentials_file.expanduser().read_text(encoding='utf-8'))
        for key in ('LAS_API_KEY', 'DEEPSEEK_API_KEY'):
            value = values.get(key)
            if value:
                if not isinstance(value, str):
                    raise ValueError(f'{key} must be a string')
                os.environ[key] = value
    run_dir = None
    for flag in ('--submit-run', '--poll-run', '--relabel-run', '--sync-run'):
        if flag in remaining:
            run_dir = Path(remaining[remaining.index(flag) + 1]).resolve()
            break
    profile = remaining[remaining.index('--profile') + 1] if '--profile' in remaining else 'default'

    def execute():
        with execution_lock(str(config_path) + '.lock'):
            job_fields.refresh(run_dir, profile, config_path=config_path)
        if '--sync-run' in remaining:
            job_fields.sync(run_dir)
            return 0
        sys.argv = [sys.argv[0], *remaining]
        try:
            return pipeline.main()
        finally:
            prepared = run_dir or pipeline.PREPARED_RUN_DIR
            if prepared and '--dry-run' not in remaining:
                job_fields.sync(prepared)

    if run_dir:
        with execution_lock(run_dir / '.execution.lock'):
            return execute()
    return execute()


if __name__ == '__main__':
    raise SystemExit(main())
