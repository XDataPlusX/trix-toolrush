"""Stage payload 'after' sources into a working copy of the hermes-agent tree.

Given a payload (ours or the upstream ToolRush one) this applies every
selected lane's 'after' function source into the corresponding file of a
staging checkout by AST-located replacement (decorators accounted, class
nesting respected, original indentation preserved). Optional --rename rules
(word-boundary regex) adapt upstream references, e.g.
toolrush_rpc=trix_rush_rpc.

The staging tree is then fed to build_payload.py, which diffs it against the
git baseline to produce our payload. Nothing here touches a live install.

Usage:
    python scripts/stage_rows.py --payload payload.json --lanes snapshot,rpc \
        --root /path/to/staging-tree [--rename toolrush_rpc=trix_rush_rpc ...]
"""
import argparse
import ast
import json
import re
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from build_payload import funcs, module_name  # noqa: E402


def module_file(root, module):
    rel = module.replace('.', '/')
    for candidate in (Path(root / f'{rel}.py'), Path(root / rel / '__init__.py')):
        if candidate.is_file():
            return candidate
    sys.exit(f'cannot resolve module {module!r} under {root}')


def find_span(tree, qualname):
    """Return (start_lineno, end_lineno, col_offset) for a qualname in a parsed tree."""
    parts = qualname.split('.')
    node = None
    stack = [(tree, 0)]

    while stack:
        current, depth = stack.pop()
        for child in ast.iter_child_nodes(current):
            if isinstance(child, ast.ClassDef):
                if child.name == parts[depth]:
                    if depth == len(parts) - 2:
                        for member in ast.iter_child_nodes(child):
                            if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                                    and member.name == parts[-1]:
                                node = member
                                break
                    else:
                        stack.append((child, depth + 1))
            elif depth == len(parts) - 1 and isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and child.name == parts[-1]:
                node = child
                break
        if node is not None:
            break
    if node is None:
        return None
    start = min([node.lineno] + [d.lineno for d in node.decorator_list])
    return start, node.end_lineno, node.col_offset


def apply_renames(text, renames):
    for old, new in renames:
        text = re.sub(rf'\b{re.escape(old)}\b', new, text)
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload', required=True)
    parser.add_argument('--lanes', required=True, help='comma-separated lane names')
    parser.add_argument('--root', required=True, help='staging hermes-agent checkout')
    parser.add_argument('--rename', action='append', default=[],
                        help='old=new word-boundary rename applied to after-sources')
    args = parser.parse_args()

    payload = json.loads(Path(args.payload).read_text())
    renames = [tuple(rule.split('=', 1)) for rule in args.rename]
    root = Path(args.root)
    applied = 0

    for lane in args.lanes.split(','):
        rows = payload['lanes'].get(lane)
        if rows is None:
            sys.exit(f'lane {lane!r} not in payload')
        for row in rows:
            path = module_file(root, row['module'])
            text = path.read_text(encoding='utf-8')
            extracted = funcs(text)
            if row['qualname'] not in extracted:
                sys.exit(f'{path}: qualname {row["qualname"]!r} not found '
                         f'(new-function insertion is not supported yet)')
            after = apply_renames(textwrap.dedent(row['after']), renames)
            tree = ast.parse(text)
            span = find_span(tree, row['qualname'])
            if span is None:
                sys.exit(f'{path}: cannot locate span for {row["qualname"]!r}')
            start, end, col = span
            lines = text.splitlines(keepends=True)
            indent = ' ' * col
            replacement = textwrap.indent(after.rstrip('\n') + '\n', indent)
            new_text = ''.join(lines[:start - 1]) + replacement + ''.join(lines[end:])
            ast.parse(new_text)  # must stay syntactically valid
            if funcs(new_text).get(row['qualname']) != after:
                sys.exit(f'{path}: roundtrip mismatch for {row["qualname"]!r}')
            path.write_text(new_text, encoding='utf-8')
            applied += 1
            print(f'staged {lane}/{row["qualname"]} -> {path}')

    print(f'applied {applied} rows')


if __name__ == '__main__':
    main()
