"""One-click launcher. Reuses the Brain and opens Miko's native home."""
from pathlib import Path
from contextlib import contextmanager
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

root = Path(__file__).resolve().parent
launch_config = root / 'miko_launch.json'
if launch_config.exists():
    runtime_dir = json.loads(launch_config.read_text(encoding='utf-8')).get('runtime_dir','')
    if runtime_dir:
        root = Path(runtime_dir)


def health():
    try:
        with urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=2) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None


def is_miko_17(status):
    return (isinstance(status, dict)
            and status.get('brain') == 'Miko Brain'
            and str(status.get('version', '')).startswith('AUTONOMY-17.'))


@contextmanager
def launch_lock():
    """Serialize two clicks so they cannot start two Brain processes."""
    if os.name != 'nt':
        yield
        return
    import msvcrt
    lock_dir = root / 'miko_logs'
    lock_dir.mkdir(exist_ok=True)
    with (lock_dir / 'launch.lock').open('a+b') as handle:
        handle.seek(0)
        if not handle.read(1):
            handle.seek(0)
            handle.write(b'\0')
            handle.flush()
        handle.seek(0)
        for _ in range(120):
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                time.sleep(.25)
                handle.seek(0)
        else:
            raise RuntimeError('Another Miko launcher is taking too long.')
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def windows_args(command):
    """Use Windows' own command-line parser for exact project matching."""
    shell32 = ctypes.windll.shell32
    kernel32 = ctypes.windll.kernel32
    shell32.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    count = ctypes.c_int()
    pointer = shell32.CommandLineToArgvW(command, ctypes.byref(count))
    if not pointer:
        return []
    try:
        return [pointer[index] for index in range(count.value)]
    finally:
        kernel32.LocalFree(ctypes.cast(pointer, ctypes.c_void_p))


def running_game_pid(project):
    """Find this project's running Godot game, excluding its editor window."""
    if os.name != 'nt':
        return None
    query = ('[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); '
             'Get-CimInstance Win32_Process | '
             'Where-Object { $_.Name -like "Godot*.exe" } | '
             'Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress')
    try:
        result = subprocess.run(['powershell', '-NoProfile', '-Command', query],
                                capture_output=True, encoding='utf-8',
                                errors='replace', timeout=8, check=True)
        processes = json.loads(result.stdout) if result.stdout.strip() else []
    except (subprocess.SubprocessError, ValueError):
        return None
    if isinstance(processes, dict):
        processes = [processes]
    target = os.path.normcase(str(project.resolve()))
    for process in processes:
        command = process.get('CommandLine') or ''
        if not command:
            continue
        args = windows_args(command)
        if any(arg.lower() in ('--editor', '-e') for arg in args[1:]):
            continue
        for index, arg in enumerate(args[1:], 1):
            path = None
            if arg == '--path' and index + 1 < len(args):
                path = args[index + 1]
            elif arg.startswith('--path='):
                path = arg[len('--path='):]
            if path and os.path.normcase(str(Path(path).resolve())) == target:
                return int(process['ProcessId'])
    return None


def godot_executable():
    configured = os.getenv('MIKO_GODOT_EXE','')
    config = root / 'miko_launch.json'
    if config.exists():
        configured = json.loads(config.read_text(encoding='utf-8')).get('godot_executable', configured)
    if configured and Path(configured).is_file():
        return configured
    found = shutil.which('godot') or shutil.which('godot4')
    if found:
        return found
    for folder in (Path.home()/'OneDrive'/'Documents', Path.home()/'Documents', root):
        for path in folder.glob('Godot*/*/Godot*.exe'):
            if 'console' not in path.name.lower():
                return str(path)
        for path in folder.glob('Godot*/Godot*.exe'):
            if 'console' not in path.name.lower():
                return str(path)
    return None


def launch():
    print('Miko 17 — Device-ready companion', flush=True)
    current = health()
    if current and not is_miko_17(current):
        print('Port 5000 belongs to a different or older service. Stop it and start Miko again.')
        input('Press Enter to close...')
        return 1
    if not current:
        log_dir = root / 'miko_logs'
        log_dir.mkdir(exist_ok=True)
        log = (log_dir / ('brain_'+time.strftime('%Y%m%d_%H%M%S')+'.log')).open('a',encoding='utf-8')
        env = dict(os.environ, PYTHONUTF8='1', PYTHONUNBUFFERED='1')
        subprocess.Popen([sys.executable,'-X','utf8',str(root/'miko_brain.py')],
            cwd=root, env=env, stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        for _ in range(60):
            time.sleep(.25)
            current = health()
            if current:
                break
        if not current:
            print('Miko could not start. See the newest log in:',log_dir)
            input('Press Enter to close...')
            return 1
        if not is_miko_17(current):
            print('Port 5000 answered, but it is not Miko Brain 17. See:', log_dir)
            input('Press Enter to close...')
            return 1
    exe = godot_executable()
    project = root / 'miko-3d'
    if exe and (project/'project.godot').is_file():
        game_pid = running_game_pid(project)
        if game_pid is None:
            log_dir=root/'miko_logs'
            log_dir.mkdir(exist_ok=True)
            game_log=log_dir/('godot_'+time.strftime('%Y%m%d_%H%M%S')+'.log')
            subprocess.Popen([exe,'--path',str(project),'--log-file',str(game_log)],cwd=project,
                             stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        else:
            print('Miko game is already open (PID', game_pid, ').')
    else:
        print('Open miko-3d/project.godot in Godot and press F5.')
    if '--browser' in sys.argv or os.getenv('MIKO_OPEN_BROWSER') == '1':
        webbrowser.open('http://127.0.0.1:5000/voice')
    print('Ready. In the Miko window: hold SPACE to talk, release for a reply. F8 opens optional browser voice.')
    return 0


def main():
    with launch_lock():
        return launch()


if __name__=='__main__':
    raise SystemExit(main())
