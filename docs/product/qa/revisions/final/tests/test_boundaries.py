"""Keep future components independent as features are added."""
import ast
from pathlib import Path

import pytest


@pytest.mark.parametrize('package,forbidden', [
    ('core', ('server', 'ingestion', 'admin')),
    ('server', ('ingestion', 'admin')),
    ('ingestion', ('server', 'admin')),
])
def test_package_dependency_boundaries(package, forbidden):
    root = Path(__file__).resolve().parents[1] / 'src' / package
    for file in root.rglob('*.py'):
        for node in ast.walk(ast.parse(file.read_text())):
            imports = []
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ''
                imports = [module, *(module + '.' + alias.name for alias in node.names)]
            assert not any(name == prefix or name.startswith(prefix + '.')
                           for name in imports for prefix in forbidden), str(file)


def test_default_data_path_stays_under_project():
    from core.config import LOCAL_DATA_DIR, PROJECT_ROOT
    assert PROJECT_ROOT == Path(__file__).resolve().parents[1]
    assert LOCAL_DATA_DIR == PROJECT_ROOT / 'var'
