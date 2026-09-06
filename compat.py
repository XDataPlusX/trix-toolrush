"""Version-checked Trix ToolRush update survival; never edits upstream files.

Port of ToolRush v2 compat.py (github.com/OnlyTerp/toolrush, MIT) with
per-Python payload selection for mixed local (3.11) / Docker (3.13) fleets.

Laws inherited from upstream:
- every payload blob (helpers, before/after sources) is sha256-verified;
- a lane is preflighted entirely before the first mutation (atomic install);
- any unknown upstream change refuses that lane with a loud warning;
- existing imported function references stay valid (in-place __code__ swap);
- no upstream file on disk is ever written.
"""
import hashlib
import importlib
import importlib.util
import inspect
import json
import logging
from pathlib import Path
import sys
import types

logger = logging.getLogger(__name__)
BASE = Path(__file__).parent


class CompatibilityError(RuntimeError):
    pass


def payload_candidates():
    """Per-Python payload first (payload-311.json / payload-313.json), then
    the generic payload.json. First existing file wins."""
    minor = ''.join(map(str, sys.version_info[:2]))
    return [BASE / f'payload-{minor}.json', BASE / 'payload.json']


def verify_blob(blob, expected):
    if hashlib.sha256(blob).hexdigest() != expected:
        raise CompatibilityError('Trix ToolRush payload hash mismatch')
    return blob


def fingerprint(code):
    def constant(v):
        if isinstance(v, types.CodeType):
            return fingerprint(v)
        if isinstance(v, (tuple, frozenset)):
            return [constant(x) for x in v]
        return repr(v)
    # Ignore source locations and adaptive bytecode specialization. Names,
    # arguments, constants, exception table and executable bytecode must match.
    return (code.co_code.hex(), code.co_argcount, code.co_posonlyargcount,
            code.co_kwonlyargcount, code.co_flags, code.co_names, code.co_varnames,
            code.co_freevars, code.co_cellvars, [constant(x) for x in code.co_consts],
            code.co_exceptiontable.hex())


def compile_function(source, module, name):
    scope = dict(module.__dict__)
    import ast
    flags = 0
    try:
        tree = ast.parse(Path(module.__file__).read_text(encoding='utf-8'))
        import __future__
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module == '__future__':
                for feature in node.names:
                    flags |= getattr(__future__, feature.name).compiler_flag
    except (AttributeError, OSError):
        pass
    exec(compile(source, '<trix-toolrush-compatible-function>', 'exec', flags=flags,
                 dont_inherit=True), scope)
    obj = scope[name]
    if not isinstance(obj, types.FunctionType):
        return obj
    bound = types.FunctionType(obj.__code__, module.__dict__, obj.__name__,
                               obj.__defaults__, obj.__closure__)
    bound.__kwdefaults__ = obj.__kwdefaults__
    bound.__annotations__ = obj.__annotations__
    return bound


def source_digest(source):
    import ast, textwrap
    tree = ast.parse(textwrap.dedent(source))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                first.value.value = inspect.cleandoc(first.value.value)
    return hashlib.sha256(ast.dump(tree, include_attributes=False).encode()).hexdigest()


def matches(current, source, compiled):
    if (getattr(current, '_trix_rush_installed_code', None) is current.__code__
            and getattr(current, '_trix_rush_source_digest', None) == source_digest(source)):
        return True
    try:
        return source_digest(inspect.getsource(current)) == source_digest(source)
    except (OSError, TypeError):
        return fingerprint(current.__code__) == fingerprint(compiled.__code__)


def prepare_rows(rows):
    pending = []
    for row in rows:
        module = importlib.import_module(row['module'])
        target = module
        parts = row['qualname'].split('.')
        for part in parts[:-1]:
            target = getattr(target, part)
        name = parts[-1]
        current = getattr(target, name, None)
        after = compile_function(row['after'], module, name)
        if not isinstance(after, types.FunctionType):
            raise CompatibilityError('Payload function name mismatch')
        after._trix_rush_source_digest = source_digest(row['after'])
        if current is None and row['before'] is None:
            pending.append((target, name, None, after))
            continue
        if not isinstance(current, types.FunctionType):
            raise CompatibilityError(f'{row["module"]}.{row["qualname"]}: incompatible target')
        if current.__code__.co_freevars or after.__code__.co_freevars:
            raise CompatibilityError('Closure-bearing target is not patchable')
        if matches(current, row['after'], after):
            continue
        if row['before'] is None:
            raise CompatibilityError(f'{row["qualname"]}: new upstream method conflicts')
        before = compile_function(row['before'], module, name)
        if not matches(current, row['before'], before):
            raise CompatibilityError(f'{row["module"]}.{row["qualname"]}: upstream changed')
        pending.append((target, name, current, after))
    return pending


def install_rows(rows):
    pending = prepare_rows(rows)  # entire lane preflight BEFORE first mutation
    for target, name, current, after in pending:
        if current is None:
            after._trix_rush_installed_code = after.__code__
            setattr(target, name, after)
        else:
            # Existing imports/registered handlers still hold this function.
            current.__code__ = after.__code__
            current.__defaults__ = after.__defaults__
            current.__kwdefaults__ = after.__kwdefaults__
            current._trix_rush_installed_code = current.__code__
            current._trix_rush_source_digest = after._trix_rush_source_digest
    return len(pending)


def load_helpers(payload):
    for name, item in payload['helpers'].items():
        path = BASE / item['file']
        blob = verify_blob(path.read_bytes(), item['sha256'])
        if name in sys.modules:
            current = getattr(sys.modules[name], '__file__', None)
            if current:
                verify_blob(Path(current).read_bytes(), item['sha256'])
            continue
        try:
            spec = importlib.util.find_spec(name)
        except (ValueError, ImportError):
            spec = None
        if spec is not None:
            if spec.origin:
                verify_blob(Path(spec.origin).read_bytes(), item['sha256'])
            continue
        parent, leaf = name.rsplit('.', 1)
        package = importlib.import_module(parent)
        module = types.ModuleType(name)
        module.__file__ = str(path)
        module.__package__ = parent
        sys.modules[name] = module
        try:
            exec(compile(blob, str(path), 'exec'), module.__dict__)
        except Exception:
            sys.modules.pop(name, None)
            raise
        setattr(package, leaf, module)


def install(payload_path=None):
    candidates = [Path(payload_path)] if payload_path else payload_candidates()
    path = next((p for p in candidates if p.exists()), None)
    if path is None:
        raise CompatibilityError(
            'No payload found for Python %s (looked for %s)'
            % (sys.version_info[:2], ', '.join(str(p.name) for p in candidates)))
    payload = json.loads(path.read_text(encoding='utf-8'))
    if sys.version_info[:2] != tuple(payload['python']):
        raise CompatibilityError(
            'Python bytecode version changed (%s vs payload %s); rebuild the payload '
            'under the target runtime (see build_payload.py)'
            % (sys.version_info[:2], tuple(payload['python'])))
    for rows in payload['lanes'].values():
        for row in rows:
            for field in ('before', 'after'):
                if row[field] is not None:
                    verify_blob(row[field].encode(), row[field + '_sha256'])
    load_helpers(payload)
    result = {}
    for name, rows in payload['lanes'].items():
        try:
            result[name] = {'status': 'ready', 'patched': install_rows(rows)}
        except Exception as exc:
            result[name] = {'status': 'degraded', 'reason': str(exc)}
            logger.warning('Trix ToolRush %s disabled on changed upstream: %s', name, exc)
    return result
