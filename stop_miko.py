"""Close this installed Miko game and Brain after any send completes."""
from pathlib import Path
import json
import subprocess
import time
import os
import start_miko as launcher

root=Path(__file__).resolve().parent
config=root/'miko_launch.json'
if config.exists():
    root=Path(json.loads(config.read_text(encoding='utf-8')).get('runtime_dir',str(root)))
for _ in range(40):
    state=root/'miko_brain_state.json'
    pending=(json.loads(state.read_text(encoding='utf-8')).get('pending_external_action') or {}) if state.exists() else {}
    if pending.get('status')!='executing':break
    time.sleep(1)
else:
    raise SystemExit('Miko is still sending an email. Try closing again after it finishes.')
game = launcher.running_game_pid(root/'miko-3d')
if game:
    subprocess.run(['taskkill','/PID',str(game),'/F'],check=True,stdout=subprocess.DEVNULL)
query='[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); Get-CimInstance Win32_Process | Where-Object { $_.Name -match "^python(w)?\\.exe$" } | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress'
result=subprocess.run(['powershell','-NoProfile','-Command',query],capture_output=True,encoding='utf-8',errors='replace',check=True)
processes=json.loads(result.stdout) if result.stdout.strip() else []
if isinstance(processes,dict):processes=[processes]
path=os.path.normcase(str(root/'miko_brain.py'))
for process in processes:
    args=launcher.windows_args(process.get('CommandLine') or '')
    if any(os.path.normcase(str(Path(arg).resolve()))==path for arg in args[1:] if arg and not arg.startswith('-')):
        subprocess.run(['taskkill','/PID',str(process['ProcessId']),'/F'],check=True,stdout=subprocess.DEVNULL)
print('Miko closed. If you opened the optional browser voice tab, you can close it too.')
