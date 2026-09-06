"""Read-only Trix ToolRush integrity and compatibility check. No models, no writes.

Locates the Hermes checkout (local Linux layout, Docker /opt/hermes, or
explicit --root), verifies payload helper hashes, and dry-runs every lane
through the compatibility preflight. --smoke additionally boots the plugin
through the real PluginManager in THIS fresh process only and, once the rpc
lane ships, exercises a real parallel() batch.

Exit codes: 0 ok, 2 problems.
"""
import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

P = Path(__file__).parent


def _looks_like_hermes(candidate):
    try:
        pyproject = candidate / 'pyproject.toml'
        return pyproject.is_file() and 'name = "hermes-agent"' in pyproject.read_text(
            encoding='utf-8', errors='replace')
    except OSError:
        return False


def discover_hermes_root(explicit=None):
    """Explicit path > TRIX_HERMES_ROOT > HERMES_HOME/hermes-agent >
    sibling of the plugin's parent (local ~/.hermes layout) > /opt/hermes
    (official Docker image) > cwd."""
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get('TRIX_HERMES_ROOT'):
        candidates.append(Path(os.environ['TRIX_HERMES_ROOT']))
    if os.environ.get('HERMES_HOME'):
        candidates.append(Path(os.environ['HERMES_HOME']) / 'hermes-agent')
    candidates.append(P.parent.parent / 'hermes-agent')
    candidates.append(Path('/opt/hermes'))
    candidates.append(Path.cwd())
    for candidate in candidates:
        if _looks_like_hermes(candidate):
            return candidate.resolve()
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--smoke', action='store_true',
                        help='boot the plugin via the real PluginManager (fresh process only)')
    parser.add_argument('--root', help='path to the hermes-agent checkout')
    args = parser.parse_args()

    result = {'payload': {}, 'lanes': {}, 'python': list(sys.version_info[:2]),
              'restart_note': 'This fresh-process check does not activate '
                              'already-running gateways.'}
    ok = True
    try:
        root = discover_hermes_root(args.root)
        if root is None:
            raise RuntimeError(
                'hermes-agent checkout not found; pass --root or set TRIX_HERMES_ROOT')
        result['hermes_root'] = str(root)
        sys.path.insert(0, str(root))

        spec = importlib.util.spec_from_file_location('trix_toolrush_doctor_compat',
                                                      P / 'compat.py')
        compat = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(compat)

        payload_path = next((p for p in compat.payload_candidates() if p.exists()), None)
        if payload_path is None:
            raise RuntimeError('no payload file found (expected payload.json or a '
                               'payload-<majorminor>.json variant)')
        payload = json.loads(payload_path.read_text(encoding='utf-8'))
        result['payload_file'] = payload_path.name
        if sys.version_info[:2] != tuple(payload['python']):
            result['payload_python'] = payload['python']
            result['payload_python_note'] = (
                'payload was built under another Python; lanes would refuse to load. '
                'Rebuild with build_payload.py under this runtime.')

        for name, row in payload['helpers'].items():
            compat.verify_blob((P / row['file']).read_bytes(), row['sha256'])
            result['payload'][name] = 'verified'

        for lane, rows in payload['lanes'].items():
            try:
                result['lanes'][lane] = {'status': 'compatible',
                                         'pending_function_patches': len(compat.prepare_rows(rows))}
            except Exception as exc:
                result['lanes'][lane] = {'status': 'degraded', 'reason': str(exc)}
                ok = False
        if not payload['lanes']:
            result['note'] = 'skeleton payload: no lanes shipped yet (M1)'

        if args.smoke:
            from hermes_cli.plugins import PluginManager, PluginManifest
            manager = PluginManager()
            manager._load_plugin(PluginManifest(name='trix-toolrush', version='0.1.0',
                                                source='user', path=str(P), key='trix-toolrush'))
            loaded = manager._plugins['trix-toolrush']
            assert loaded.enabled and not loaded.error, loaded.error
            status = loaded.module._COMPAT_STATUS
            assert isinstance(status, dict), status
            result['boot'] = status if status else {'status': 'ready', 'lanes': 0}
    except Exception as exc:
        ok = False
        result['error'] = str(exc)

    result['ok'] = ok
    print(json.dumps(result, indent=2))
    return 0 if ok else 2


if __name__ == '__main__':
    sys.exit(main())
