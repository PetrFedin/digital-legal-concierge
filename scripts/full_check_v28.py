import compileall
import subprocess
import sys

print('V28 full check started')
if not compileall.compile_dir('app', quiet=1):
    raise SystemExit('compile failed')
for cmd in [
    [sys.executable, 'scripts/init_db.py'],
    [sys.executable, 'scripts/production_acceptance.py'],
]:
    print('running:', ' '.join(cmd))
    subprocess.check_call(cmd)
print('full_check_v28 OK')
