"""Install the complete update with an intact, recoverable data snapshot."""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import ctypes
import subprocess
import sys
import time
import winreg
import zipfile

source=Path(__file__).resolve().parent
with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as key:
    target=Path(os.path.expandvars(winreg.QueryValueEx(key,'Desktop')[0])).resolve()
protected=['miko_brain_state.json','miko_credentials.dat','miko_integrations.json',
           'miko_brain_state_before_migration.json','miko_device_settings.json']
sources=['miko_brain.py','miko_realtime.py','miko_realtime_tools.py','miko_vision.py','miko_voice.html',
         'check_miko_camera.py','Check Miko Camera.cmd','miko_perception.py','miko_deps.py',
         'miko_log.py','miko_physical.py','miko_gestures.py','miko_behavior.py','miko_language.py','miko_vision_worker.py','collect_miko_logs.py','Collect Miko Logs.cmd',
         'start_miko.py','Start Miko.cmd','stop_miko.py','Stop Miko.cmd','requirements_miko.txt']
excluded={'.godot','.git','__pycache__'}
for name in sources:
    if not (source/name).is_file():raise RuntimeError('Incomplete update: '+name)
for name in ['miko-3d/project.godot','device/bridge.py','device/protocol.py','vision_models/face_detection_yunet_2023mar.onnx','vision_models/face_landmarker.task']:
    if not (source/name).is_file():raise RuntimeError('Incomplete update: '+name)
for name in protected[:3]:
    if not (target/name).is_file():raise RuntimeError('Existing Miko data missing: '+name)
json.loads((target/'miko_brain_state.json').read_text(encoding='utf-8'))
TEXT_SUFFIXES={'.py','.gd','.tscn','.txt','.md','.cmd','.json','.html','.c','.h','.yml','.godot','.gdshader','.cjs','.import','.uid','.cfg','.svg'}

def _manifest_digest(file):
    # The manifest is computed from LF sources; Git on Windows may check text
    # files out with CRLF. Binary files are hashed exactly.
    data=file.read_bytes()
    if file.suffix.lower() in TEXT_SUFFIXES or file.name.startswith('.'):
        data=data.replace(b'\r\n',b'\n')
    return hashlib.sha256(data).hexdigest()

manifest=source/'MANIFEST_SHA256.txt'
if manifest.exists():
    for line in manifest.read_text(encoding='utf-8').splitlines():
        digest,name=line.split('  ',1)
        file=(source/name).resolve()
        if not file.is_relative_to(source) or not file.is_file() or _manifest_digest(file)!=digest:
            raise RuntimeError('Package integrity check failed: '+name)

def pending_send():
    return (json.loads((target/'miko_brain_state.json').read_text(encoding='utf-8')).get('pending_external_action') or {}).get('status')=='executing'

for _ in range(40):
    if not pending_send():break
    time.sleep(1)
else:raise RuntimeError('Miko is sending an email. Finish that send before installing.')

# Use exact Windows argument parsing. An unrelated Python/Godot process or an
# editor is never stopped merely because its command mentions "miko".
spec=importlib.util.spec_from_file_location('miko_launcher_for_install',source/'start_miko.py')
launcher=importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)
query=('[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); '
       'Get-CimInstance Win32_Process | Where-Object { $_.Name -match "^python(w)?\\.exe$" } | '
       'Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress')
result=subprocess.run(['powershell','-NoProfile','-Command',query],capture_output=True,encoding='utf-8',errors='replace',check=True)
processes=json.loads(result.stdout) if result.stdout.strip() else []
if isinstance(processes,dict):processes=[processes]
brain_pids=[]
brain_path=os.path.normcase(str(target/'miko_brain.py'))
for process in processes:
    args=launcher.windows_args(process.get('CommandLine') or '')
    if any(os.path.normcase(str(Path(arg).resolve()))==brain_path for arg in args[1:] if arg and not arg.startswith('-')):
        brain_pids.append(int(process['ProcessId']))
game_pid=launcher.running_game_pid(target/'miko-3d')

backup=Path.home()/'OneDrive'/'Documents'/'MIKO_BACKUPS'/('before_17_8_'+time.strftime('%Y%m%d_%H%M%S'))
backup.mkdir(parents=True,exist_ok=False)
for name in protected+sources+['miko_launch.json']:
    if (target/name).is_file():shutil.copy2(target/name,backup/name)
with zipfile.ZipFile(backup/'godot_project_before.zip','w',zipfile.ZIP_DEFLATED,compresslevel=3) as archive:
    for file in (target/'miko-3d').rglob('*'):
        relative=file.relative_to(target/'miko-3d')
        if file.is_file() and not any(part in excluded for part in relative.parts):archive.write(file,relative)
if (target/'device').is_dir():
    shutil.copytree(target/'device',backup/'device',ignore=shutil.ignore_patterns('__pycache__'),dirs_exist_ok=True)
if pending_send():raise RuntimeError('An email started during backup. Finish it and install again. Backup: '+str(backup))
if game_pid:
    subprocess.run(['taskkill','/PID',str(game_pid),'/F'],check=True,stdout=subprocess.DEVNULL)
for pid in brain_pids:
    subprocess.run(['taskkill','/PID',str(pid),'/F'],check=True,stdout=subprocess.DEVNULL)
if pending_send():raise RuntimeError('A send was still in progress. Sources were not replaced. Backup: '+str(backup))

digests={}
for name in protected:
    file=target/name
    if file.is_file():
        digests[name]=hashlib.sha256(file.read_bytes()).hexdigest()
        if (backup/name).read_bytes()!=file.read_bytes():shutil.copy2(file,backup/(file.stem+'_at_stop'+file.suffix))
def _replace_file(src,dst):
    # Windows refuses to overwrite a read-only or hidden file (OneDrive and
    # earlier tools can leave dotfiles hidden). Clear those attributes and
    # retry briefly in case OneDrive is syncing the file.
    for attempt in range(6):
        try:
            return shutil.copy2(src,dst)
        except PermissionError:
            if not os.path.exists(dst) or attempt==5:raise
            try:
                os.chmod(dst,stat.S_IWRITE|stat.S_IREAD)
                if os.name=='nt':ctypes.windll.kernel32.SetFileAttributesW(str(dst),0x80)  # FILE_ATTRIBUTE_NORMAL
            except OSError:
                pass
            time.sleep(0.5)

for name in sources:_replace_file(source/name,target/name)
for folder in ['miko-3d','device','vision_models']:
    shutil.copytree(source/folder,target/folder,dirs_exist_ok=True,copy_function=_replace_file,
                    ignore=shutil.ignore_patterns('.godot','.git','__pycache__','*.log','.editorconfig','.gitattributes','.gitignore'))
config=json.loads((source/'miko_launch.json').read_text(encoding='utf-8'))
config['runtime_dir']=str(target)
(target/'miko_launch.json').write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
for name,digest in digests.items():
    if hashlib.sha256((target/name).read_bytes()).hexdigest()!=digest:raise RuntimeError('Persistent data changed: '+name)
(backup/'INSTALL_VERIFICATION.json').write_text(json.dumps({'data_preserved':True,'files':digests,'version':'17.8'},indent=2),encoding='utf-8')
# Import the new 3D assets now so the first start shows Miko, not a grey window.
exe=launcher.godot_executable()
if exe and hasattr(launcher,'ensure_imported'):
    print('Preparing the 3D files...')
    launcher.ensure_imported(exe,target/'miko-3d')
# Sight is optional: MediaPipe/OpenCV let Miko see the owner. A failure here
# never blocks the update; Miko then simply runs with less (or no) sight.
try:
    sys.path.insert(0,str(source))
    import miko_deps
    sight=miko_deps.ensure_vision_packages()
    print({'mediapipe':'Sight: full (expressions, hand gestures, gaze).','opencv':'Sight: basic (faces, waves, changes).',
           'none':'Sight: off for now (no internet?). Run Check Miko Camera.cmd later.'}[sight])
except Exception as error:
    print('Sight packages were not installed:',type(error).__name__)
print('Miko 17.8 installed. Memory, history, Gmail credentials and device pairing were preserved.')
print('Backup:',backup)
print('Open Start Miko.cmd on your Desktop. Hold SPACE in Miko to talk.')
