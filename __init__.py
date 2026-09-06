"""Trix ToolRush — Linux/Docker acceleration lanes for Hermes Agent.

Linux port of ToolRush v2 (github.com/OnlyTerp/toolrush), shipped as a
standalone Hermes plugin. Core Hermes owns tool semantics and safety gates;
this plugin only replaces transports (native rg argv, batched parallel read
RPC, streaming warm shell) through in-memory, function-level, hash-verified
compatibility patches. Upstream drift degrades a lane loudly instead of
overwriting new code. Nothing on disk inside the Hermes checkout is ever
modified.

Kill-switches (all fail-closed): remove from plugins.enabled, config section
`trix-toolrush: {enabled: false}` (or `toolrush:`), env TRIX_TOOLRUSH_*=0 /
legacy TOOLRUSH_*=0, HERMES_SAFE_MODE=1. Reload requires a fresh host
process; never hot-patch live turns.
"""
import importlib.util
import logging
from pathlib import Path

_COMPAT_STATUS = None


def register(ctx=None):
    global _COMPAT_STATUS
    if _COMPAT_STATUS is None:
        try:
            spec = importlib.util.spec_from_file_location(
                '_trix_toolrush_compat', Path(__file__).with_name('compat.py'))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _COMPAT_STATUS = module.install()
        except Exception as exc:
            _COMPAT_STATUS = {'bootstrap': {'status': 'degraded', 'reason': str(exc)}}
            logging.getLogger(__name__).warning(
                'Trix ToolRush compatibility bootstrap disabled: %s', exc)
            return
    # Future lanes (warm shell terminal transport) attach here, gated on the
    # snapshot lane being ready. The skeleton payload ships no lanes.
    return _COMPAT_STATUS
