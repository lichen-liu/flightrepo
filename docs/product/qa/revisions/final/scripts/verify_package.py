"""Verify the built wheel's assets and run its API from outside the checkout."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wheel', type=Path)
    parser.add_argument('--database', type=Path, default=Path('var/connections-demo.db'))
    parser.add_argument('--output', type=Path, default=Path('var/package-verification.json'))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    verified = []
    with TemporaryDirectory(prefix='flyji-package-') as directory:
        unpacked = Path(directory)/'package'
        with ZipFile(args.wheel) as wheel:
            for asset in (root/'src').rglob('*'):
                if asset.is_file() and '__pycache__' not in asset.parts and asset.suffix != '.pyc':
                    name = str(asset.relative_to(root/'src'))
                    assert wheel.read(name) == asset.read_bytes(), f'Missing or outdated wheel file: {name}'
                    verified.append(name)
            wheel.extractall(unpacked)
        environment = dict(os.environ, PYTHONPATH=str(unpacked),
                           FLIGHT_DATABASE_PATH=str(args.database.resolve()), PYTHONDONTWRITEBYTECODE='1')
        code = '''import json,sys
from pathlib import Path
import core.config,server.api
from fastapi.testclient import TestClient
root=Path(sys.argv[1])
assert Path(core.config.__file__).is_relative_to(root)
assert Path(server.api.__file__).is_relative_to(root)
with TestClient(server.api.app) as client:
    assert client.get('/').status_code==200
    assert client.get('/assets/app.mjs').status_code==200
    assert client.get('/assets/vendor/world-110m.geojson').status_code==200
    assert client.get('/health/ready').status_code==200
    result=client.post('/v1/itineraries/search/local',json={
        'origin':'YYZ','destination':'CDG','departure_date':'2026-10-17',
        'arrival_by_local':'2026-10-21T23:59','departure_timezone':'America/Toronto',
        'arrival_timezone':'Europe/Paris'})
    assert result.status_code==200,result.text
    assert result.json()['items'] and result.json()['diagnostics']['exhaustive']
print(json.dumps({'imported_from_wheel':True,'working_directory_outside_checkout':True,
                  'root_assets_readiness_and_local_search_passed':True}))
'''
        process = subprocess.run([sys.executable, '-c', code, str(unpacked)], cwd=directory,
                                 env=environment, text=True, capture_output=True, check=True)
    report = {'wheel': args.wheel.name, 'wheel_sha256': hashlib.sha256(args.wheel.read_bytes()).hexdigest(),
              'verified_package_files': sorted(verified), 'installed_api_probe': json.loads(process.stdout),
              'source_sha256': {'scripts/verify_package.py': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
