import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_production_logging_does_not_dump_local_values(tmp_path):
    env = {**os.environ, 'ROOT_URL': 'https://www.astronet.ru', 'PYTHONPATH': str(ROOT / 'src')}
    script = tmp_path / 'log_failure.py'
    script.write_text('''
from main import logger

def operation():
    private_value = "SYNTHETIC-LOCAL-SECRET"
    raise RuntimeError(len(private_value))

try:
    operation()
except RuntimeError:
    logger.exception("Operation failed")
''')
    result = subprocess.run(
        [sys.executable, '-B', str(script)],
        env=env, capture_output=True, text=True, check=True,
    )
    assert 'Operation failed' in result.stdout
    assert 'SYNTHETIC-LOCAL-SECRET' not in result.stdout


def test_container_entrypoint_uses_installed_python_without_uv(tmp_path):
    cmd = next(line for line in (ROOT / 'Dockerfile').read_text().splitlines() if line.startswith('CMD '))
    venv_bin = tmp_path / '.venv' / 'bin'
    venv_bin.mkdir(parents=True)
    (venv_bin / 'python').symlink_to(sys.executable)
    (tmp_path / 'main.py').write_text('print("Service started")\n')

    result = subprocess.run(
        json.loads(cmd.removeprefix('CMD ')), cwd=tmp_path, env={**os.environ, 'PATH': ''},
        capture_output=True, text=True, check=True,
    )

    assert result.stdout.strip() == 'Service started'
