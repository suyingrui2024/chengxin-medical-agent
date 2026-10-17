"""Reproduce the read-only upstream inspection checkout at the recorded commit."""
import json
import subprocess
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    lock = json.loads((root / 'vendor/upstream.lock.json').read_text())
    path = root / lock['source_directory']
    commit = lock['inspected_source_commit']
    if path.exists():
        actual = subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()
        if actual != commit:
            raise SystemExit(f'Existing checkout differs: {actual}; will not overwrite it')
    else:
        subprocess.run(['git', 'clone', '--no-checkout', '--filter=blob:none', lock['repository'], str(path)], check=True)
        subprocess.run(['git', '-C', str(path), 'checkout', '--detach', commit], check=True)
    print(f'DSH source verified: {commit}')


if __name__ == '__main__':
    main()
