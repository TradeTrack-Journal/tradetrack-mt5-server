"""Run the production worker without a PowerShell/console-window dependency."""
import argparse
import ctypes as c
from ctypes import wintypes as w
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from app.collector.node_agent import InventoryAgent, load_config
from app.collector.server_preparation import start_terminal
from app.collector.windows_inventory import WindowsTerminal


def load_token(path):
    # ConvertFrom-SecureString's default Windows format is a DPAPI hex blob,
    # scoped to the same service user's Windows profile. Never persist plaintext.
    class Blob(c.Structure):
        _fields_ = [('size', w.DWORD), ('data', c.POINTER(c.c_ubyte))]
    raw = bytes.fromhex(Path(path).read_text(encoding='utf-8-sig').strip())
    buffer = (c.c_ubyte * len(raw)).from_buffer_copy(raw)
    source, result = Blob(len(raw), buffer), Blob()
    crypt = c.WinDLL('crypt32', use_last_error=True)
    kernel = c.WinDLL('kernel32', use_last_error=True)
    crypt.CryptUnprotectData.argtypes = [c.POINTER(Blob), c.c_void_p, c.c_void_p,
                                        c.c_void_p, c.c_void_p, w.DWORD, c.POINTER(Blob)]
    crypt.CryptUnprotectData.restype = w.BOOL
    kernel.LocalFree.argtypes = [c.c_void_p]
    kernel.LocalFree.restype = c.c_void_p
    if not crypt.CryptUnprotectData(c.byref(source), None, None, None, None, 1, c.byref(result)):
        raise RuntimeError('TOKEN_DECRYPT_FAILED')
    try:
        return c.string_at(result.data, result.size).decode('utf-16-le')
    finally:
        c.memset(result.data, 0, result.size)
        kernel.LocalFree(result.data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config-directory', required=True)
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--watchdog', action='store_true')
    args = parser.parse_args()
    directory = Path(args.config_directory).resolve()
    repo = Path(__file__).resolve().parent.parent
    native = WindowsTerminal()
    role = 'health-watchdog' if args.watchdog else 'production'

    def emit(state, **fields):
        with (directory / f'{role}-supervisor.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(state=state, at=datetime.now(timezone.utc).isoformat(), **fields)) + '\n')

    # A Python-owned mutex survives neither exit nor crash; an orphaned parent
    # shell cannot block the next launch. The task must use the same Windows user.
    with native.inventory_lock(str(directory / f'{role}-supervisor')):
        while True:
            if (directory / 'worker.drain').exists():
                if args.check_only:
                    raise RuntimeError('WORKER_DRAINED')
                time.sleep(5)
                continue
            child_env = None
            try:
                config = load_config(directory / 'agent.json')
                if config['apiBaseUrl'] != 'https://tradetrack-api.fly.dev':
                    raise RuntimeError('UNEXPECTED_API')
                token = load_token(directory / 'node-token.dpapi')
                InventoryAgent(config, token).connect()  # Exact API path/config validation.
                if args.check_only:
                    emit('preflight_ok')
                    return
                for slot in ([] if args.watchdog else config['slots']):
                    if (Path(slot['dataPath']) / '.server-preparing').exists():
                        raise RuntimeError('UNFINISHED_PREPARATION')
                    if not native.find_process(slot['executablePath'])[0]:
                        start_terminal(slot['executablePath'])
                child_env = dict(os.environ, MT5_AGENT_TOKEN=token,
                                 MT5_CLOCK_OBSERVATIONS_DIR=str(directory.parent / 'clock-observations'))
                environment_file = directory / 'runtime-environment.json'
                if environment_file.exists():
                    settings = json.loads(environment_file.read_text(encoding='utf-8-sig'))
                    if settings.get('MT5_SENTRY_DSN'):
                        child_env['MT5_SENTRY_DSN'] = settings['MT5_SENTRY_DSN']
                token = None
                python = Path(sys.executable).with_name('python.exe')
                stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
                if args.watchdog:
                    stamp += '.watchdog'
                command = ([str(python), '-m', 'scripts.watch_mt5_health',
                            '--config', str(directory / 'agent.json'),
                            '--log', str(directory / 'health-watchdog.jsonl')] if args.watchdog else
                           [str(python), '-m', 'scripts.run_mt5_worker',
                            '--config', str(directory / 'agent.json'), '--drain-file', str(directory / 'worker.drain'),
                            '--builder', str(directory.parent / 'server-builder' / 'terminal64.exe')])
                with (directory / f'{stamp}.stdout.log').open('ab') as out, (directory / f'{stamp}.stderr.log').open('ab') as err:
                    child = subprocess.Popen(command,
                        cwd=repo, env=child_env, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.ABOVE_NORMAL_PRIORITY_CLASS)
                    child_env.pop('MT5_AGENT_TOKEN', None)
                    emit('worker_started', pid=child.pid, release=repo.name)
                    # wait() waits for this process, without waiting for inherited
                    # terminal output streams to close.
                    code = child.wait()
                    emit('worker_exited', exitCode=code)
            except Exception as exc:
                emit('supervisor_error', exceptionType=type(exc).__name__)
                if args.check_only:
                    raise SystemExit(1) from None
            finally:
                token = None
                if child_env is not None:
                    child_env.pop('MT5_AGENT_TOKEN', None)
            time.sleep(30)


if __name__ == '__main__':
    main()
