"""Rebuild payload.json from a patched Hermes checkout.

The root tree must ALREADY contain the Trix ToolRush patches (a working copy
of hermes-agent where our modified function sources are in place, in-tree).
The baseline ref must resolve to the unpatched upstream commit in that
repository's git history. Every function whose AST-extracted source differs
from the baseline becomes a payload row {module, qualname, before, after,
sha256}; brand-new functions get before=None. Helpers are hashed straight
from lib/.

IMPORTANT: build the payload under the SAME Python minor version as the
target runtime (the payload is stamped with it). Local Linux venv = 3.11,
official Docker image = 3.13; ship payload-311.json / payload-313.json
variants for mixed fleets.

Usage:
    python build_payload.py --root /path/to/patched-hermes-agent \
        [--baseline HEAD] [--out payload.json]
"""
import argparse
import ast
import hashlib
import json
import subprocess
import sys
import textwrap
from pathlib import Path

P = Path(__file__).parent

# lane name -> list of module files (paths relative to the checkout root)
# keep in sync with the lanes that ship in the payload
LANES = {
    'snapshot': ['tools/environments/base.py'],
    'rpc': ['tools/code_execution_tool.py', 'tools/code_kernel.py'],
}

HELPER_DIRS = {'lib/tools': 'tools', 'lib/agent': 'agent'}


def funcs(text):
    """Extract {Class.method: source} (decorators included) from a module."""
    out = {}

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start = min([child.lineno] + [d.lineno for d in child.decorator_list])
                lines = text.splitlines()
                source = textwrap.dedent('\n'.join(lines[start - 1:child.end_lineno])) + '\n'
                out[prefix + child.name] = source
            elif isinstance(child, ast.ClassDef):
                walk(child, prefix + child.name + '.')
    walk(ast.parse(text), '')
    return out


def module_name(rel):
    parts = rel.with_suffix('').parts
    return '.'.join(parts[:-1]) if parts[-1] == '__init__' else '.'.join(parts)


def git_show(root, ref, rel):
    proc = subprocess.run(['git', '-C', str(root), 'show', f'{ref}:{rel.as_posix()}'],
                          capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else None


def sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


def collect_helpers():
    helpers = {}
    for directory, package in HELPER_DIRS.items():
        for path in sorted((P / directory).glob('*.py')):
            if path.name == '__init__.py':
                continue
            helpers[f'{package}.{path.stem}'] = {
                'file': path.relative_to(P).as_posix(),
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            }
    return helpers


def build_rows(root, baseline):
    lanes = {}
    for lane, files in LANES.items():
        rows = []
        for rel in files:
            rel = Path(rel)
            current_text = (root / rel).read_text(encoding='utf-8')
            current = funcs(current_text)
            base_text = git_show(root, baseline, rel)
            base = funcs(base_text) if base_text is not None else {}
            for qualname, after in current.items():
                before = base.get(qualname)
                if before == after:
                    continue
                rows.append({
                    'module': module_name(rel),
                    'qualname': qualname,
                    'before': before,
                    'after': after,
                    'before_sha256': sha256(before) if before is not None else None,
                    'after_sha256': sha256(after),
                })
        lanes[lane] = rows
    return lanes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, help='patched hermes-agent checkout')
    parser.add_argument('--baseline', default='HEAD',
                        help='git ref of the unpatched upstream (default HEAD)')
    parser.add_argument('--out', default=None, help='output payload file')
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not (root / 'pyproject.toml').is_file():
        sys.exit(f'not a hermes-agent checkout: {root}')
    out = Path(args.out) if args.out else P / f'payload-{sys.version_info[0]}{sys.version_info[1]}.json'

    payload = {
        'python': list(sys.version_info[:2]),
        'helpers': collect_helpers(),
        'lanes': build_rows(root, args.baseline),
    }
    out.write_text(json.dumps(payload, indent=1), encoding='utf-8')
    print(json.dumps({'out': str(out), 'python': payload['python'],
                      'helpers': len(payload['helpers']),
                      'lanes': {k: len(v) for k, v in payload['lanes'].items()}}, indent=2))


if __name__ == '__main__':
    main()
