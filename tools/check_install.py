"""Build a wheel, install into a clean venv and exercise its console command."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv

ROOT = Path(__file__).resolve().parents[1]
MODULE = "composelens"
with tempfile.TemporaryDirectory(prefix=MODULE + '-install-') as directory:
    work = Path(directory)
    subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-deps', '--wheel-dir', str(work), str(ROOT)], check=True)
    env = work / 'venv'
    venv.EnvBuilder(with_pip=True).create(env)
    bins = env / ('Scripts' if os.name == 'nt' else 'bin')
    python = bins / ('python.exe' if os.name == 'nt' else 'python')
    command = bins / (MODULE + ('.exe' if os.name == 'nt' else ''))
    wheel, = work.glob('*.whl')
    subprocess.run([str(python), '-m', 'pip', 'install', '--no-deps', '--no-index', str(wheel)], check=True, cwd=work)
    clean = dict(os.environ)
    clean.pop('PYTHONPATH', None)
    subprocess.run([str(command), '--help'], check=True, cwd=work, env=clean, stdout=subprocess.PIPE)
    if MODULE == 'hookreplay':
        args = ['--db', str(work / 'events.sqlite'), 'list']
    elif MODULE == 'queuelab':
        path = work / 'scenario.json'
        path.write_text(json.dumps({'version':1,'retry':{'max_attempts':1,'backoff':'fixed','delay_ms':0},'jobs':[{'id':'sample','failures_before_success':0}]}))
        args = [str(path)]
    elif MODULE == 'runsift':
        path = work / 'bundle.json'
        path.write_text(json.dumps({'version':1,'run_id':'sample','jobs':[]}))
        args = [str(path), str(path)]
    else:
        # Resolution is covered separately by real Docker integration tests.
        args = ['-b', 'missing.yml', '-c', 'missing.yml']
    result = subprocess.run([str(command), *args], cwd=work, env=clean, capture_output=True, text=True)
    expected = 2 if MODULE == 'composelens' else 0
    assert result.returncode == expected, result
    json.loads(result.stderr if expected else result.stdout)
    print(MODULE + ': isolated wheel installation and command checks passed')
