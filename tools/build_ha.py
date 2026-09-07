"""Build the installable HA archive from the same library used by the CLI."""
import hashlib
import json
from pathlib import Path
import shutil
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / 'home-assistant/custom_components/catch_control'
SOURCE = ROOT / 'library/src/catch_control'
VENDOR = COMPONENT / '_vendor/catch_control'


def build():
    library_version = tomllib.loads((ROOT / 'library/pyproject.toml').read_text())['project']['version']
    integration_version = json.loads((COMPONENT / 'manifest.json').read_text())['version']
    VENDOR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / 'LICENSE', COMPONENT / 'LICENSE')
    for stale in VENDOR.iterdir():
        if stale.is_dir():
            shutil.rmtree(stale)
        else:
            stale.unlink()
    hashes = {}
    for path in sorted(SOURCE.glob('*.py')):
        shutil.copyfile(path, VENDOR / path.name)
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (COMPONENT / '_vendor/__init__.py').write_text('"""Generated library bundle; refresh with tools/build_ha.py."""\n')
    (COMPONENT / '_vendor/library.json').write_text(json.dumps({'distribution': 'catch-control', 'version': library_version, 'sha256': hashes}, indent=2) + '\n')
    dist = ROOT / 'dist'
    dist.mkdir(exist_ok=True)
    target = dist / f'catch-control-home-assistant-{integration_version}.zip'
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(COMPONENT.rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts and (path.suffix in {'.py', '.json', '.yaml'} or path.name == 'LICENSE'):
                archive.write(path, path.relative_to(ROOT / 'home-assistant'))
    print(target)


if __name__ == '__main__':
    build()
