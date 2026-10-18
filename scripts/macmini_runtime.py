"""Run the checked-in producer or an operator-installed pinned private patch.

The adjacent manifest pins the source bytes and the original repository code.
Feed commits remain owned by the clean production checkout. No network code
or commands from logs/configuration are evaluated.
"""
import hashlib
import json
from pathlib import Path
import sys
import types
import os
import fcntl
import tempfile
import importlib.util
import importlib
from datetime import datetime, timezone

MODULES = ('feed_resilience', 'cloud_whisper_pilot', 'cloud_whisper_queue', 'macmini_health', 'macmini_recovery', 'macmini_feed')


def load(directory):
    if not (directory/'manifest.json').exists():
        root = directory.parent
        if directory.name != 'scripts' or not (root/'.git').exists() or not (root/'config/sources.json').is_file():
            raise RuntimeError('Producer runtime requires a repository or a pinned manifest')
        sys.path.insert(0, str(directory))
        modules = {name: importlib.import_module(name) for name in MODULES}
        queue = modules['cloud_whisper_queue']
        queue.RUNTIME_ENTRY = directory/'macmini_runtime.py'
        queue.LOCAL_VALIDATION_REPAIR = True
        return modules['macmini_feed'], modules['macmini_recovery']
    manifest = json.loads((directory/'manifest.json').read_text())
    root = Path(manifest['repository']).resolve()
    for relative, expected in manifest['baselines'].items():
        path = root/relative
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError('Production source changed; review the private patch before running')
    for name in MODULES:
        source = directory/(name+'.py')
        if hashlib.sha256(source.read_bytes()).hexdigest() != manifest['patches'][name]:
            raise RuntimeError('Private patch checksum mismatch')
    for name, expected in manifest.get('assets', {}).items():
        if Path(name).name != name or hashlib.sha256((directory/name).read_bytes()).hexdigest() != expected:
            raise RuntimeError('Private policy checksum mismatch')
    sys.path.insert(0, str(root/'scripts'))
    for name in MODULES:
        source = directory/(name+'.py')
        module = types.ModuleType(name)
        module.__file__ = str(root/'scripts'/(name+'.py'))
        sys.modules[name] = module
        exec(compile(source.read_bytes(), module.__file__, 'exec'), module.__dict__)
    queue = sys.modules['cloud_whisper_queue']
    queue.RUNTIME_ENTRY = directory/'entry.py'
    queue.LOCAL_VALIDATION_REPAIR = True
    if 'sources.json' in manifest.get('assets', {}):
        queue.SOURCE_POLICY = directory/'sources.json'
    return sys.modules['macmini_feed'], sys.modules['macmini_recovery']


def startup_failure(error):
    """Checksum/import errors must also reach the existing private channel."""
    state = Path(sys.argv[sys.argv.index('--state-dir')+1]).resolve()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(state, 0o700)
    path = state/'runtime-error.json'
    def save(data):
        descriptor, name = tempfile.mkstemp(dir=state, prefix='.runtime-error-')
        try:
            with os.fdopen(descriptor, 'w') as stream:
                json.dump(data, stream)
                stream.flush(); os.fsync(stream.fileno())
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)
    with (state/'runtime-error.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        kind = type(error).__name__
        from signal_alerts import deliver
        def send(text):
            if '--notifier-module' not in sys.argv:
                return {'status': 'credentials_unavailable'}
            module_path = sys.argv[sys.argv.index('--notifier-module')+1]
            spec = importlib.util.spec_from_file_location('existing_runtime_notifier', module_path)
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            return module.send(text)
        receipt = deliver(state, 'runtime', kind, True,
            'AI Signal 启动失败，自动抓取已停止。\n运行版本或依赖需要人工检查。\n原队列和故障现场已保留。', send)
        if receipt:
            save(dict(receipt, status='blocked', error_type=kind))



def main():
    runner, recovery = load(Path(__file__).resolve().parent)
    if len(sys.argv) == 3 and sys.argv[1] == '--_worker':
        return sys.modules['cloud_whisper_queue'].worker(Path(sys.argv[2]))
    x_result = {}
    if '--health-only' in sys.argv and '--auto-repair' in sys.argv:
        try:
            state = Path(sys.argv[sys.argv.index('--state-dir')+1])
            x_result = recovery.recover_x(state)
            print(json.dumps({'x_recovery': x_result}))
        except Exception as exc:
            x_result = {'status': 'publication_check_unavailable'}
            print(json.dumps({'x_recovery': 'unavailable', 'error_type': type(exc).__name__}))
        if '--notifier-module' in sys.argv:
            status = x_result.get('status', 'publication_check_unavailable')
            if status not in {'not_due', 'already_running', 'attempt_started', 'job_running'}:
                module = sys.argv[sys.argv.index('--notifier-module')+1]
                good = status == 'ok'
                runner.health.notify(state, {'scope': 'x', 'publish': True,
                                    'status': 'completed' if good else 'failed',
                                    'failure_kind': None if good else 'x_'+status}, module)
    code = runner.main()
    if '--auto-repair' in sys.argv:
        state = Path(sys.argv[sys.argv.index('--state-dir')+1])
        proxy = sys.argv[sys.argv.index('--audio-proxy')+1] if '--audio-proxy' in sys.argv else None
        result = recovery.sync_public_cache(runner.ROOT, state, proxy=proxy,
                                            x_generated_at=x_result.get('generated_at'),
                                            x_content_sha256=x_result.get('content_sha256'))
        print(json.dumps({'public_cache': result}))
        if result and set(result.values()) <= {'verified', 'pending'} and '--notifier-module' in sys.argv:
            module = sys.argv[sys.argv.index('--notifier-module')+1]
            pending = 'pending' in result.values()
            runner.health.notify(state, {'scope': 'cache', 'publish': True,
                                'status': 'failed' if pending else 'completed',
                                'failure_kind': 'public_mirror_stale' if pending else None}, module)
    return code


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        try:
            startup_failure(exc)
        except Exception:
            pass  # Never mask the failure exit if local evidence cannot be saved.
        print(json.dumps({'status': 'runtime_blocked', 'error_type': type(exc).__name__}))
        raise SystemExit(1)
