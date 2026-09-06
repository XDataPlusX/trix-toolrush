# ToolRush v2 — инвентаризация оригинала (для Linux-порта)

Источник: клон github.com/OnlyTerp/toolrush в /tmp/opencode/toolrush (временный).
Дата: 2026-09-06. Плагин: `v2/plugin/`, payload ~175KB, python [3,11].

## payload.json — все 25 патчей по лейнам

### files (15)
| module.qualname | New? | Суть патча |
|---|---|---|
| `tools.file_operations.ShellFileOperations._resolve_command` | | шорт-кат `command -v rg` через нативный контекст + кэш |
| `.._native_rg_context` | NEW | гейт + `toolrush_rg.native_context()` → `(cwd, run_env, executable)` или None |
| `.._native_rg` | NEW | прямой запуск rg: argv, `toolrush_rg.run_rg`, bounded capture, OSError→fallback |
| `.._modified_rg_capability_error` | | probe `rg --version` сначала нативно |
| `..read_file` | | перевод shell-пути через `_local_native_path` перед native read |
| `.._local_native_path` | NEW | трансляция пространств путей; на non-Windows — pass-through |
| `.._native_read_enabled` | | замена `sys.platform != "win32"`-гейта на toolrush-гейт |
| `.._read_file_native` | | CRLF-нормализация нативно прочитанной страницы |
| `.._assemble_read_result` | | +1 строка если нет финального newline |
| `..search` | | `test -e` → `os.path.exists(native)` |
| `.._zero_match_probe` | | пробы через helper: native argv → shell fallback |
| `.._search_files_rg` | | file discovery нативно (`--files -g … --sortr=modified`) |
| `.._search_with_rg` | | главное: `--sort=path` стабильная пагинация, `-e pattern --`, +1 sentinel, полный native argv, CRLF, честный truncated, count-пагинация |
| `tools.file_tools._filter_read_blocked_search_results` | | мемоизация read-block гарда на вызов |
| `tools.file_tools.search_tool` | | удалён v1 fast-lane (чистый Python поиск); strict-JSON `_hint` |

### rpc (5)
- `tools.code_execution_tool.generate_hermes_tools_module` — stub `parallel()` в hermes_tools (RPC `__toolrush_parallel__`)
- `tools.code_execution_tool._rpc_server_loop` — серверная диспетчеризация батча (до allow-list check, с бюджетом)
- `tools.code_execution_tool.build_execute_code_schema` — документация parallel() в схеме
- `tools.code_kernel.CellAuthority.dispatch` — `ctx.copy().run()` для конкурентных вызовов
- `tools.code_kernel._rpc_forever` — `for_batch` привязка cell authority раз на батч

### admission (1)
- `agent.tool_dispatch_helpers._is_readonly_terminal_command` — строгий классификатор `toolrush_admission.readonly()` перед legacy-списком

### snapshot (4) — fail-closed фиксы
- `_export_dump_excluding_session_vars` — belt-unset `BUZZ_*` всегда
- `BaseEnvironment._snapshot_excluded_passthrough_names` — latch `_snapshot_exclusion_broken` + warning
- `BaseEnvironment.init_session` — при latch: `exit 1` вместо dump
- `BaseEnvironment._wrap_command` — пропуск re-dump при latch

## Хелперы (lib/)
- **toolrush_runtime.py** (22 стр): `enabled(key, legacy_env)` — env-откат `0/false/no/off`, затем `load_config_readonly()['toolrush']`, любое исключение → False. Ключи: enabled, fast_read, fast_search, warm_shell, parallel_reads.
- **toolrush_rg.py** (203): `native_context(ops)` — только LocalEnvironment; env из atomic snapshot (≤1MiB), `declare -x` парсинг выборочных ключей (RIPGREP_CONFIG_PATH, HOME, XDG_CONFIG_HOME, PATH, LANG, LC_ALL), отказ от `rg()`-функций/алиасов; **Windows: `.exe`-дополнение, `;`-PATH, MSYS-трансляции, cargo/winget fallback**. `run_rg()` — reader-thread + bounded queue(4), interrupt→130, deadline→124, byte/line budget (лимит строк = exit 0 как `| head`), utf-8/replace, CRLF→LF.
- **toolrush_shell.py** (202): WarmShell = `bash --noprofile --norc -s` через `_find_bash()`; pipes (не PTY); broker-env allow-list (Windows-варянты: SYSTEMROOT, WINDIR, COMSPEC, PATHEXT, MSYS_*); frame-протокол с uuid-маркерами; rewrite snapshot-коммита (mktemp→O_EXCL, mv→`: > ready`); PATH `;`→`:` перекодировка; WarmHandle — streaming ProcessHandle, `os.replace` синхронно ДО completion; kill → смерть брокера.
- **toolrush_rpc.py** (80): `execute_read_batch` на RPC-потоке: 1..16 calls, READ_TOOLS={read_file, search_files, web_search, web_extract} ∩ allowed, атомарный бюджет, ThreadPoolExecutor(min(4,n)), input order, двойной JSON-decode tolerance.
- **agent/toolrush_admission.py** (125): readonly() — отказ от `` ` ``, `$(`, `>`, `<`, `${!`, `${=`, одиночного `&`; посегментно (`; && || |`); ~40 простых утилит + per-command правила (git read-only подкоманды, sed только `-n Np`, curl bounded, sort/uniq/jq/rg ограничения, python/node только --version/-V/--help).

## Механика plugin/compat/doctor
- `__init__.py::register(ctx)` → compat.install() изолированно по пути; snapshot-ready → terminal lane; lane-патчинг idempotent (`_toolrush_v2` маркеры), non-blocking lock, никакого retry submitted command.
- `compat.py`: sha256 verify всех before/after; AST source_digest (docstrings cleandoc, комментарии/whitespace не важны); bytecode fingerprint fallback; `prepare_rows` — preflight всей лейна, refusal на closure-таргетах; инсталляция через `current.__code__ = after.__code__` (импорты остаются валидны); helpers грузятся blob'ами с verify, отказ при shadowing.
- `doctor.py`: verify 5 хелперов + per-lane prepare; `--smoke`: настоящий PluginManager._load_plugin в свежем процессе + реальный execute_code с parallel() × 2 read_file.

## Тесты (13 файлов, ~206 кейсов) — Windows-специфика
- `test_file_tools.py` (48): TestWindowsMsysPathResolution — windows_only.
- `test_search_files_engine_selection.py` (27): 3 windows_only (winget rg.exe, MSYS-remote, drive breadth).
- `test_search_zero_match_and_multipath.py` (18): portable.
- `test_toolrush_native_read.py` (6): Windows-namespace тест — на Linux no-op.
- `test_toolrush_native_search.py` (7+14): portable, нужен rg.
- `test_toolrush_plugin_v2.py` (11): warm-сьют = Windows Bash broker; на Linux не работает из-за `_IS_WINDOWS` гейта.
- `test_toolrush_rg_bounds.py` (8): portable (child=sys.executable).
- `test_toolrush_rpc.py` (10): portable (TCP loopback).
- `test_toolrush_search.py` (11): portable.
- `test_toolrush_snapshot_failclosed.py` (11): dual-platform (есть POSIX-ветки), 1 win32-lane.
- `test_toolrush_update_survival.py` (5): хардкод `C:/dev/AppData/Local/hermes/plugins/toolrush`.
- `run_agent/test_parallel_terminal_and_wire.py` (24): C:/ литералы (inert).
- `run_agent/test_tool_batch_segmentation.py` (30): 1 windows_only.
- `run_agent/test_toolrush_admission_safety.py` (38 кейсов): portable.

## Бенчмарки
- v1 lab (корень): bench_baseline / bench_proto (fresh task_id против dedup-кэша), bench_search_* (match SETS), bench_term_* (byte-identical), bench_dispatch* (вердикт: 4.44ms — load-bearing, STOP).
- v2: `evidence/benchmark_handlers.py` (--plugin флаг, 7 workload'ов, чередование on/off, cold+warm16, median/p95, strict-JSON equality); `benchmark_batches.py` (TCP RPC, sequential/parallel чередование, паритет; 2.06x/1.44x/0.98x regression/3.34x controlled); `benchmark_terminal.py` (stock|old|new, отдельные процессы, честно: repaired медленнее unsafe-old из-за синхронного коммита). Хардкоды `C:/dev/...`.

## build_survival_payload.py / simulate_update.py
- Build: AST-извлечение всех функций (с декораторами) из патченного дерева; baseline = `git show HEAD:<file>` (кэш); rows = диффы; helpers копируются с sha256; `python=[3,11]`.
- Simulate: in-memory revert (delattr новых / `before.__code__` подмена), install() → 4 lanes ready + 25 patched, реальный read/search/RPC прогон, дисковые хэши неизменны.

## v1-лаборатория (корень репо)
- `toolrush.py`: fast_read in-process (замена 5 shell round-trips), mtime-keyed кэш, DaemonThreadPoolExecutor, per-path lock'и; kill-switches.
- `toolrush_search.py`: чистый Python os.walk+re — **v2 отверг этот подход** (реализация меньшей семантики ≠ корректность).
- `toolrush_exec.py`: персистентный bash с TRX-маркерами — предок WarmShell; `exec_spawn()` — negative control.

## Методология валидации (replicate checklist)
1. Контракт до кода (VAL-*, expect(N)).
2. Intent из первоисточников (ID сообщений, не пересказ).
3. Baseline с точными артефактами (команды, exit, XML, failing IDs).
4. Профилирование до оптимизации (cProfile-диссекция).
5. Дифференциальная корректность на реальных entry points (on/off gates, match SETS).
6. SAFE: fail-closed, admission≠approval, отказ ≠ отклонение работы.
7. NEG: настоящие negative controls в изолированных процессах + хэш-контроль.
8. PERF: paired, чередование порядка, cold/warm, раскрытые регрессии.
9. INTEGRATE: fresh-process smoke, failure-SETS идентичны, kill-switch обязателен.
10. DELIVER: независимый ревью, пробразы исходников, runbook, limits.
