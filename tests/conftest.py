"""Trix ToolRush tests.

Skeleton tests run standalone. Lane tests need a Hermes checkout — resolved
the same way doctor.py does — and install the plugin lanes IN-MEMORY in this
test process only (the live install and its disk are never touched).
"""
import importlib.util
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from doctor import discover_hermes_root  # noqa: E402

# Collection-time imports in lane tests need the Hermes checkout importable
# before any fixture runs; resolve it once here (best effort).
HERMES_ROOT = discover_hermes_root()
if HERMES_ROOT is not None and str(HERMES_ROOT) not in sys.path:
    sys.path.insert(0, str(HERMES_ROOT))


def load_compat():
    spec = importlib.util.spec_from_file_location('trix_test_compat', PLUGIN / 'compat.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_runtime():
    spec = importlib.util.spec_from_file_location(
        'trix_test_runtime', PLUGIN / 'lib' / 'tools' / 'trix_rush_runtime.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='session')
def hermes_root():
    root = discover_hermes_root()
    if root is None:
        pytest.skip('hermes-agent checkout not found (pass TRIX_HERMES_ROOT)')
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


_LANES_STATUS = None


def ensure_lanes_installed():
    """Install every shipped lane in-memory in THIS process (collection time).

    The live install and its disk are never touched. Skips the whole module
    when no Hermes checkout is found; fails loudly when a lane degrades
    against the discovered tree (drift must be visible, not skipped).
    """
    global _LANES_STATUS
    if HERMES_ROOT is None:
        pytest.skip('hermes-agent checkout not found (set TRIX_HERMES_ROOT)')
    if _LANES_STATUS is None:
        _LANES_STATUS = load_compat().install()
    degraded = {k: v for k, v in _LANES_STATUS.items() if v.get('status') != 'ready'}
    if degraded:
        pytest.fail(f'lanes degraded against this tree: {degraded}')
    return _LANES_STATUS


@pytest.fixture(scope='session')
def plugin_lanes():
    return ensure_lanes_installed()
