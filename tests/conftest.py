"""Trix ToolRush tests.

Lane tests (M2+) need a Hermes checkout — they resolve it the same way
doctor.py does and skip with a clear reason when absent. Skeleton tests run
standalone.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent


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
