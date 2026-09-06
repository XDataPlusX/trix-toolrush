"""Trix ToolRush runtime gates — fail-closed lane switches.

Config (in the owning profile's config.yaml), primary section with upstream
fallback:

    trix-toolrush:
      enabled: true        # master switch
      fast_search: true    # per-lane switches
      parallel_reads: true
      warm_shell: true
      fast_read: true

Env kill-switches are checked first and can only DISABLE (never enable).
Primary names TRIX_TOOLRUSH_*; legacy TOOLRUSH_* names from upstream docs are
honored identically. Values 0/false/no/off disable the lane.

Any failure (missing config, import error, malformed section) disables the
lane: gates fail closed, acceleration is always optional.
"""
import os

_FALSY = {'0', 'false', 'no', 'off'}
_MASTER = 'enabled'
_SECTIONS = ('trix-toolrush', 'toolrush')


def _env_killed(env_names):
    if isinstance(env_names, str):
        env_names = (env_names,)
    for name in env_names:
        value = os.environ.get(name)
        if value is not None and value.strip().lower() in _FALSY:
            return True
    return False


def enabled(key, env_names=()):
    """True only if lane `key` is explicitly enabled in config and not env-killed.

    `key` is the config lane name (fast_search, parallel_reads, warm_shell,
    fast_read). `env_names` are kill-switch variable names, e.g.
    ('TRIX_TOOLRUSH_SEARCH', 'TOOLRUSH_SEARCH').
    """
    try:
        if _env_killed(env_names):
            return False
        from hermes_cli.config import load_config_readonly
        config = load_config_readonly() or {}
        section = None
        for name in _SECTIONS:
            candidate = config.get(name)
            if isinstance(candidate, dict):
                section = candidate
                break
        if section is None:
            return False
        return section.get(_MASTER) is True and section.get(key) is True
    except Exception:
        return False
