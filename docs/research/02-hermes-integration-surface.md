# Живой Hermes — поверхность интеграции (для Trix ToolRush)

Дата: 2026-09-06. Инсталляция: /home/rafail/.hermes/hermes-agent.
HEAD 2b55ded1ac (2026-09-03), v2026.8.3-7034-g2b55ded1ac, pyproject 0.21.0.
Прод-venv: `venv/` (Py 3.11.15; гейтвеи: `venv/bin/python -m hermes_cli.main [--profile X] gateway run`).
`.venv/` — uv-managed дубль. **pytest нет нигде**; `scripts/run_tests.sh` требует pytest.

## Плагины (hermes_cli/plugins.py, 7193 стр)
- Discovery порядок: bundled `<repo>/plugins/` → **user `get_hermes_home()/plugins/`** (профиль-скопированный!) → project `cwd/.hermes/plugins/` (только HERMES_ENABLE_PROJECT_PLUGINS=1) → pip entry-points.
- Профили = изолированные HERMES_HOME (`--profile` препарсится в hermes_cli/main.py:550 ДО импортов). Доказано: pix имеет свой plugins/pix-ops + `plugins.enabled: [pix-ops]`.
- plugin.yaml (PluginManifest, SUPPORTED_MANIFEST_VERSION=2): v1 поля name/version/description/author/...; v2 manifest_version/api_version/...; неизвестные поля → warn и дальше.
- Загрузка: `discover_and_load()` → `_load_plugin_scoped()` → `module.register(ctx=PluginContext)`. User/standalone = opt-in через `plugins.enabled` список; `plugins.disabled` всегда сильнее; CLI `hermes plugins enable|disable`.
- **`hermes plugins install <git-url|owner/repo> [--enable]`** — git clone --depth 1 → валидация plugin.yaml → атомарный move в `<home>/plugins/<name>` → запись source/revision. Механизм дистрибуции trix-toolrush.
- PluginContext (~30 методов): register_hook (VALID_HOOKS: pre/post_tool_call, transform_tool_result, transform_terminal_output, pre_gateway_dispatch, ...), register_tool (override=true требует `plugins.entries.<id>.allow_tool_override`), register_skill, ... invoke_hook сигнатурно-инспектирует колбэки.
- HERMES_SAFE_MODE=1 — пропуск всех плагинов.
- Кастомные top-level ключи config.yaml tolerated (прецедент known_plugin_toolsets).
- Live config: `plugins: {enabled: [openai], disabled: []}` (default), coder/trest — нет секции, pix — [pix-ops].

## Shell/terminal (tools/environments/local.py 2356 стр, base.py 1602)
- `_IS_WINDOWS = platform.system()=="Windows"` (local.py:22) — False здесь.
- `_run_bash` (local.py:2101): spawn-per-call `[bash, -c, cmd]` (`-l` для login); env = `_make_run_env(self.env)` (merge os.environ|env, strip Hermes-секретов, passthrough, PATH-дополнения, ContextVar env); `start_new_session=True`; `proc._hermes_pgid = os.getpgid(pid)`; `_resolve_safe_cwd` (issue #17558).
- `_kill_process` (local.py:2171): psutil-снапшот потомков → killpg TERM → ≤1s → killpg KILL → sweep setsid-побег (issue #84967).
- `cleanup` (local.py:2339): unlink snapshot/cwd/orphan temps.
- Snapshot-машина (base.py): `_export_dump_excluding_session_vars` (552) — unset-first дизайн против injection (#71296); `init_session` (725) — umask 077, mktemp, export dump + functions + aliases, atomic mv, cd, pwd-маркеры; `_wrap_command` (872) — source snapshot, cd || exit 126, eval cmd, **re-dump env атомарно (mktemp+mv)**, cwd-маркер, exit $__hermes_ec; `execute` (1445) — bounded_capture, timeout+grace backstop (#94285), вывод `{"output":..., "returncode":...}`.

## execute_code / hermes_tools (code_execution_tool.py 2503, code_kernel.py 1062)
- SANDBOX_ALLOWED_TOOLS = {web_search, web_extract, read_file, write_file, search_files, patch, terminal}; DEFAULT_MAX_TOOL_CALLS=50; stdout 50KB cap.
- `generate_hermes_tools_module` (485): генерирует hermes_tools.py в песочнице; UDS-транспорт (HERMES_RPC_SOCKET) или tcp на Windows; file-транспорт для remote; токен HERMES_RPC_TOKEN.
- `_rpc_server_loop` (723): newline-JSON, compare_digest токен → allowed_tools → бюджет → dispatch (model_tools.handle_function_call).
- SessionKernel (337): child-процесс + RPC-поток + reader-потоки; `_KERNELS` реестр, cap+idle-reap; CellAuthority (264) пинит contextvars/approval, retired-cell dispatch отказ.

## Файловые инструменты (file_tools.py 2966, file_operations.py 4436)
- read_file → `read_file_tool` (1657): device-guard, special-file guard, docx/pdf/xlsx extract → `file_ops.read_file`.
- search_files → `search_tool` (2587): target map grep/find → `ShellFileOperations.search` (3432).
- `_get_file_ops` (1413): по одному ShellFileOperations на task.
- `_exec` (1079) — каждый file-op = полный bash round-trip.
- `_resolve_command` (1118): `command -v` кэш; rg: re-probe on miss + Windows off-PATH скан.
- **`_native_read_enabled` (1837): POSIX + local → включено; `HERMES_NATIVE_FILE_READ` default "1" → native read АКТИВЕН** (в .env не переопределён). `_read_file_native` (1853): чистый Python chunked read.
- Остаточный shell в чтении: только fallback-пути.
- **Поиск шеллит**: `_search_files` (3784): `rg --files [--sortr=modified] -g … | head`; `_search_content` (4019) → `_search_with_rg` (4058): `--line-number --no-heading --with-filename --max-columns 2000 …` + grep fallback; `_zero_match_probe` (3674): до 3 доп. rg-проб. Вот главный headroom.

## Система
| Проверка | Результат |
|---|---|
| rg | /usr/bin/rg **13.0.0** (нет --sortr=modified, нужен ≥14) |
| bash | /usr/bin/bash 5.1.16 |
| venv | venv/ Py 3.11.15 (прод), .venv/ Py 3.11.15 (uv); pytest нет |
| HERMES_NATIVE_FILE_READ | default-on, в .env не тронут → native read активен на всех профилях |
| toolrush в дереве | ноль упоминаний (greenfield) |
| git file_tools.py | 413fd2a1fc fast discovery file ordering (2026-08-28) — источник дрейфа search_tool |
| git tool_dispatch_helpers.py | 62b2d78025 (2026-08-25) — _is_readonly_terminal_command УДАЛЁН; новая модель: `_PARALLEL_SAFE_TOOLS` frozenset (48) + `_NEVER_PARALLEL_TOOLS` (45) + `_is_mcp_tool_parallel_safe` (104) |
| temp | HERMES_HOME/cache/terminal (не /tmp), prune 72h |

## Профили
- default: ~/.hermes/config.yaml — plugins.enabled [openai], terminal.backend local, terminal.cwd /home/rafail/hermes-workspace.
- coder/pix/trest: собственные config.yaml; pix — [pix-ops]. Профили НЕ наследуют default.
- Включение trix-toolrush: `plugins.enabled: [trix-toolrush]` в config.yaml владельца; плагин в `<home>/plugins/trix-toolrush/`; `hermes plugins enable trix-toolrush -p <profile>`.

## Dry-run дрейфа (per-row, 25 патчей оригинала)
- rpc 5/5 PATCHABLE; snapshot 4/4 PATCHABLE.
- files: 12/15 PATCHABLE/MISSING; drifted 2: `tools.file_tools.search_tool`, `_filter_read_blocked_search_results` (компилируются standalone OK — дрейф именно в апстрим-исходнике).
- admission: `_is_readonly_terminal_command` MISSING — механизм заменён `_PARALLEL_SAFE_TOOLS`.

## Ключевые выводы для порта
1. Native read уже апстримнут и активен — мерить против него, не против шелла.
2. Остаточный headroom: search-транспорт (bash round-trip на каждый rg + пробы), warm shell, parallel RPC.
3. Дистрибуция = `hermes plugins install` (git-репо c plugin.yaml в корне).
4. Admission-механизм оригинала устарел — нужен M5-анализ новой модели _PARALLEL_SAFE_TOOLS.
5. rg 13 vs --sortr=modified — сохранить capability-гейт.
6. Snapshot-rewrite warm shell'а должен выводиться из живого Linux-текста _wrap_command.
