# Trix ToolRush — Linux-движок ToolRush как плагин для Hermes Agent

**Статус:** план v1 (после фазы исследования 2026-09-06). До обсуждения.
**Идея:** не разовый накат, а собственная версия движка ToolRush, портированная на Linux,
дистрибутируемая как standalone-плагин (`hermes plugins install <repo>`), с полным циклом
валидации как в оригинале (тесты + бенчмарки + negative controls + evidence).

---

## 1. Цель и честная оценка ценности

Оригинальный ToolRush v2 собран и валидирован **под Windows** (rg.exe, Git-Bash broker,
MSYS-пути, гейт `_IS_WINDOWS`). Просто «взять и подключить» нельзя: нужен порт.

Главный факт исследования: **нативное чтение файлов уже апстримнуто в Hermes на POSIX**
(`ShellFileOperations._read_file_native`, включено по умолчанию, гейт `HERMES_NATIVE_FILE_READ`,
venv-переменная не переопределена → активно на этом боксе). Поэтому хедлайн-выигрыш оригинала
(57x на чтении) на Linux **уже получен самим Hermes**. Остаточная ценность порта:

| Лейн | Что даёт на Linux | Ожидание |
|---|---|---|
| **files (search transport)** | rg запускается напрямую (argv, без bash round-trip на каждый поиск + без `test -e` probe + без 3 zero-match проб). Сейчас каждый `search_files` = полный bash + snapshot-обвязка | ~3–7x на tool-операции (по аналогии с оригиналом; на Linux spawn дешевле Windows — мерить) |
| **rpc (`parallel()`)** | Батч до 16 read-операций через один RPC в `execute_code`, ≤4 воркера, аутентификация/бюджеты сохранены | ~2x на батчах (доказано оригиналом) |
| **shell (warm bash)** | Один персистентный bash вместо spawn-per-command + streaming через pipe + синхронный atomic snapshot commit | На Linux spawn дешевле — выигрыш скромнее оригинальных 23.6x. Мерить; если налог мал — честный STOP (как wave 4 в оригинале) |
| **snapshot (fail-closed)** | 4 фикса безопасности снапшотов (belt-unset секретов, latch на сбое фильтра) | Не скорость, а корректность/fail-closed |
| **admission** | Классификатор readonly-terminal. **Внимание:** апстрим удалил `_is_readonly_terminal_command` — нужна проверка, не решена ли задача новым `_PARALLEL_SAFE_TOOLS` | Возможно, лейн устарел — решить после M5-исследования |

Дополнительно: strict-JSON конверты поиска, честная пагинация (+1 sentinel), фикс
подсчёта строк без финального newline — маленькие, но настоящие фиксы корректности.

---

## 2. Факты исследования (основа плана)

### 2.1 Живой Hermes (`/home/rafail/.hermes/hermes-agent`)
- HEAD `2b55ded1ac` (2026-09-03), версия 0.21.0, ветка main, remote NousResearch/hermes-agent.
- Продакшн-venv: `venv/` (Python 3.11.15 — совпадает с требованием payload [3,11]).
  **pytest нет ни в одном venv** — нужно доставить (`uv pip install pytest pytest-asyncio pytest-timeout`).
- `rg` = /usr/bin/rg **13.0.0** — нет `--sortr=modified` (нужен rg ≥ 14; `order="modified"` сейчас
  и так падает с понятной ошибкой — сохранить этот capability-гейт).
- bash 5.1.16. Работают 4 гейтвея: default, coder, pix, trest (профили = изолированные HERMES_HOME).

### 2.2 Дрейф апстрима vs payload оригинала (per-row dry-run, 25 патчей)
- **rpc: 5/5 PATCHABLE** — применяется как есть.
- **snapshot: 4/4 PATCHABLE** — применяется как есть.
- **files: 12/15** PATCHABLE/MISSING (MISSING = новые функции, это норма);
  **2 drifted** — `tools.file_tools.search_tool` и `_filter_read_blocked_search_results`
  (апстрим-коммит `413fd2a1fc` «fast discovery file ordering» от 2026-08-28). Эти два ряда
  выводятся заново из живого кода, а не из старого payload.
- **admission: 1 MISSING** — `_is_readonly_terminal_command` удалён апстримом;
  новая модель — frozenset `_PARALLEL_SAFE_TOOLS` + MCP opt-in.
- Whole-file дрейф `code_kernel.py`, `file_tools.py`, `environments/base.py` не мешает:
  compat сверяет **AST-дайджесты отдельных функций**, не файлы.

### 2.3 Плагин-система Hermes (механика дистрибуции)
- Плагин = `<HERMES_HOME>/plugins/<name>/` c `plugin.yaml` + `__init__.py::register(ctx)`.
- Включение: `plugins.enabled: [trix-toolrush]` в config.yaml **владельца** (профиль изолирован).
- **`hermes plugins install <git-url> [--enable]`** — официальный установщик из git-репо:
  clone → валидация plugin.yaml → атомарный перенос в plugins/ → запись в enabled.
  Это и есть механизм «накатить на другие Гермесы».
- `HERMES_SAFE_MODE=1` — глобальный рубильник всех плагинов (ещё один уровень отката).
- Произвольные top-level ключи в config.yaml tolerated (прецедент `known_plugin_toolsets`),
  значит секция `toolrush:` для per-lane гейтов легальна.
- Политика апстрима: чужие плагины не включают в дерево — standalone-репо это норма.
  Мы **не** используем `register_tool(override=True)`: патчим транспорт под теми же
  хендлерами in-memory, регистрация инструментов не меняется.

### 2.4 Что в оригинале Windows-only (список для порта)
- `toolrush_rg.py`: rg.exe-резолв, MSYS-пути, `;`-PATH, case-insensitive env, cargo/winget fallback.
- `toolrush_shell.py`: Git-Bash резолв, keep-list `SYSTEMROOT/WINDIR/COMSPEC/PATHEXT/MSYS_*`,
  PATH `;`→`:` перекодировка, `windows_hide_flags()`, rewrite текста wrapper под Git-Bash.
- `plugin/__init__.py`: гейт `local._IS_WINDOWS` (warm-лейн на Linux молча неактивен).
- Тесты: `windows_only`-кейсы, `C:/`-пути, хардкод `C:/dev/...` в тестах update-survival и докторе.

---

## 3. Архитектура Trix ToolRush

```
trix-toolrush/                     # repo root = plugin root (требование plugins install)
├── plugin.yaml                  # name: trix-toolrush, version, description
├── __init__.py                  # register(ctx): compat.install() + terminal lane
├── compat.py                    # ПОРТ как есть — чистый Python, платформенно-независим
├── payload.json                 # ПЕРЕСОБРАН под HEAD 2b55ded1ac + Linux-хелперы
├── doctor.py                    # пути параметризованы (HERMES_HOME), не хардкод
├── lib/
│   ├── tools/toolrush_rg.py     # Linux: shutil.which('rg'), POSIX env, без MSYS
│   ├── tools/toolrush_shell.py  # Linux: bash из _find_bash(), POSIX keep-list, killpg
│   ├── tools/toolrush_rpc.py    # порт без изменений (платформенно-независим)
│   └── tools/toolrush_runtime.py# гейты (toolrush.enabled / TOOLRUSH_*=0)
├── build_payload.py             # порт build_survival_payload.py: живое дерево + git-бейзлайн
├── tests/                       # реплика тестов оригинала (см. §5)
├── bench/                       # paired on/off бенчмарки (см. §6)
├── evidence/                    # результаты, negative controls, контракты VAL-*
└── README.md                    # runbook: установка, doctor, rollback
```

Принципы (унаследованы из оригинала, «законы»):
- **Fail-closed per lane**: любой дрейф апстрима → лейн деградирует с warning, работа продолжается стоком.
- **In-memory only**: ни один файл живого дерева не изменяется; `git status` чекд-аута остаётся чистым всегда.
- **Preflight всей лейна до первой мутации** (атомарность установки патчей).
- **Kill-switches везде** (§7).
- **Контракт-first**: VAL-ассёрты пишутся до кода (§6.3).

Ключевые технические решения:
1. **Payload пересобирается**, а не редактируется: применяем изменения в копии дерева,
   `build_payload.py` диффит функции против `git show HEAD:<file>` и генерирует before/after
   с sha256. Воспроизводимо и обновляемо под новые версии Hermes.
2. **Мультиверсионность (этап M9)**: каталог `payloads/<commit-or-tag>/payload.json`,
   `__init__.py` выбирает первый подходящий (per-lane try), неизвестный апстрим → громко деградирует.
   v1 держит один payload под `2b55ded1ac` (v0.21.0).
3. **Linux warm shell**: bash `--noprofile --norc -s` через `_find_bash()` (на Linux это /usr/bin/bash),
   broker-env keep-list: `PATH, HOME, LANG, LC_ALL, TERM, TMPDIR, RIPGREP_CONFIG_PATH`;
   frame-протокол и rewrite snapshot-коммита (`mktemp`/`mv` → O_EXCL-резервация + `os.replace`)
   выводятся заново из **живого текста** `_wrap_command` Linux-версии;
   убийство дерева процессов через `killpg` (паритет с POSIX `_kill_process`).
4. **files-лейн на Linux — выборочный**, не все 15 рядов: читательская часть уже нативная в апстриме,
   берём из неё только реальные фиксы (`_assemble_read_result` +1 строка без финального \n,
   `_filter_read_blocked_search_results` мемоизация гарда), остальное — search-транспорт.
5. **admission-лейн**: после M5-исследования либо ретаргет классификатора на новый механизм
   `_PARALLEL_SAFE_TOOLS`, либо вывод об устаревании (документируемый, с доказательством).

---

## 4. Этапы

| # | Этап | Содержание | Критерий готовности |
|---|---|---|---|
| M0 | Исследование | ✅ выполнено (этот документ + два отчёта агентов) | план согласован |
| M1 | Каркас | ✅ v0.1.0 — репо, compat, runtime-гейты, doctor, build_payload, 9 skeleton-тестов; plugin boot через настоящий PluginManager проверен | doctor green |
| M2 | snapshot-лейн | ✅ v0.2.0 — 4 патча + 11 тестов (порт) + end-to-end POSIX-lane; найден и обойдён латентный баг оригинального payload (класс-дефолт `_snapshot_exclusion_broken` не попадал в payload — чтения заменены на getattr) | тесты green; диск не тронут |
| M3 | rpc-лейн | ✅ v0.2.0 — 5 патчей (переименовано `__trix_rush_parallel__`/`tools.trix_rush_rpc`), 12 тестов, doctor --smoke: реальный execute_code parallel() × 2 read_file, exit 0, disk_unchanged=true; негативный контроль TRIX_TOOLRUSH_PARALLEL=0 падает по задуманной причине | smoke green |
| M4 | files-лейн | Linux `toolrush_rg.py`; патчи search-транспорта (`search`, `_zero_match_probe`, `_search_files_rg`, `_search_with_rg`, `_resolve_command` + нативный контекст); заново вывести `search_tool`/`_filter_read_blocked_search_results` из живого апстрима; гейт rg13/rg14; портировать тесты: native_search, search, rg_bounds, engine_selection, zero_match_and_multipath | дифференциальные тесты on/off: конверты strict-JSON идентичны, множества совпадений идентичны; в `_exec` заменён на AssertionError в native-путях (ни одного spawn) |
| M5 | admission-исследование + ретаргет | разобрать новую модель `_PARALLEL_SAFE_TOOLS`; существует ли вообще parallel-terminal путь; если да — портировать `toolrush_admission.py` и тесты (`test_toolrush_admission_safety.py`, `test_parallel_terminal_and_wire.py`), если нет — зафиксировать устаревание | verdict-документ с доказательством; тесты green либо лейн закрыт |
| M6 | shell-лейн (warm bash) | Linux `toolrush_shell.py` + `__init__.py` terminal lane без `_IS_WINDOWS`-гейта; портировать warm-сьют из `test_toolrush_plugin_v2.py` | warm-тесты green на Linux: exit codes, env readback, стриминг до завершения, snapshot commit синхронный (os.replace), background-команды в обход, timeout reap + восстановление |
| M7 | Бенчмарки + evidence | paired on/off: handlers (read/search 7 workload'ов), batches (rpc), terminal (stock vs warm); negative controls; baseline failure-SETS сравнение до/после | все цифры с median/p95/cold-warm в evidence/; negative controls падают по задуманной причине; regression-comparison.json: идентичные наборы failing ID |
| M8 | Дистрибуция + runbook | README (install/enable/doctor/rollback), поддержка профилей, проверка `hermes plugins install` на чистом HERMES_HOME (стенд), uninstall-инструкция | установка на стендовый профиль с нуля через официальный установщик; откат всеми способами из §7 проверен |
| M9 | (опция) мультиверсии | payload под несколько известных версий Hermes + CI-скрипт пересборки | авто-выбор payload по дайджестам; деградация на неизвестной версии |

Порядок обоснован: M2–M3 дают быструю пользу (parallel + safety) минимальным риском,
M4 — главный perf-выигрыш, M6 — самый тонкий (протокол коммита снапшота).

---

## 5. Репликация тестов оригинала (13 файлов, ~206 кейсов)

| Файл оригинала | Судьба в порте | Адаптация |
|---|---|---|
| `tools/test_toolrush_rpc.py` (10) | порт как есть | portable (TCP loopback) |
| `tools/test_toolrush_snapshot_failclosed.py` (11) | порт как есть | уже dual-platform; убрать `skipif win32` lane |
| `tools/test_toolrush_rg_bounds.py` (8) | порт как есть | portable (child = sys.executable) |
| `tools/test_toolrush_search.py` (11) | порт | match-SET дифференциалы; требует rg на PATH (есть) |
| `tools/test_toolrush_native_search.py` (7+14) | порт | требует rg; strict-JSON `_hint` |
| `tools/test_search_zero_match_and_multipath.py` (18) | порт | monkeypatch-стиль, POSIX-safe |
| `tools/test_search_files_engine_selection.py` (27) | порт частично | 3 `windows_only`-теста → Linux-эквиваленты (off-PATH rg discovery через FHS-пути, /mnt breadth) |
| `tools/test_toolrush_native_read.py` (6) | порт частично | native read уже в апстриме → тестировать только фиксы порта (+1 строка, гейты); Windows-namespace тест → drop |
| `tools/test_file_tools.py` (48) | выборочно | базовый upstream-сьют уже есть в живом дереве; брать только регрессии на наши фиксы |
| `tools/test_toolrush_plugin_v2.py` (11) | порт warm-половины | broker-сьют на Linux: bash вместо Git-Bash, killpg; гейт лейна убирает `not _IS_WINDOWS` |
| `tools/test_toolrush_update_survival.py` (5) | порт | параметризовать `PLUGIN` path (было `C:/dev/...`) |
| `run_agent/test_toolrush_admission_safety.py` (38 кейсов) | по итогам M5 | логика portable |
| `run_agent/test_parallel_terminal_and_wire.py` (24) | по итогам M5 | `C:/`-литералы → `/tmp/` |
| `run_agent/test_tool_batch_segmentation.py` (30) | smoke | upstream-сьют; `windows_only` overlap-тест → drop |

Новые тесты порта (чего не было в оригинале): Linux warm-shell edge-cases (setsid-побег,
pgid-убийство, PATH coreutils паритет), мультипрофильная установка, doctor на «чужом» дереве
(деградация на неизвестном коммите).

---

## 6. Валидация и бенчмарки (методология оригинала)

### 6.1 Бенчмарки (все на реальных файлах живого репо, без моделей)
- `bench/handlers.py` — paired on/off (`TOOLRUSH_SEARCH=1/0`), 7 workload'ов, чередование порядка
  итераций, cold-сэмпл отдельно, warm n=16, median/p95, strict-JSON equality на каждой итерации.
- `bench/batches.py` — RPC: sequential vs parallel (4 поиска / mixed / controlled-50ms),
  warm n=12, паритет результатов.
- `bench/terminal.py` — stock vs warm: builtin / python / git, отдельные процессы,
  returncode asserted, честное раскрытие дисперсии.
- `bench/dissect.py` — cProfile-диссекция одного вызова (слой-тайминги) до оптимизации.

### 6.2 Negative controls (каждый — изолированный процесс, падение по задуманной причине)
1. `TOOLRUSH_SEARCH=0` → native-search тест падает (transport снова шеллится).
2. `TOOLRUSH_PARALLEL=0` → overlap-тест падает (workers сериализованы).
3. Убран snapshot-commit → warm-тест падает (rc/env не закоммичены).
4. Небезопасный admission восстановлен → safety-тест падяет на `wget`/`curl -o`.
5. Дисковые хэши живого дерева до/после всего прогона — идентичны.

### 6.3 Контракт-first
До кода пишем `evidence/validation-contract.md` с VAL-* ассертами (по образцу expect(9)):
INTENT / BASE / DESIGN / CORRECT / SAFE / NEG / PERF / INTEGRATE / DELIVER. Тест-селекторы
не подгоняются под результат; baseline-red фиксируется, а не скрывается.

### 6.4 Брод-регрессия
Прогон живого upstream-сьюта до и после → `regression-comparison.json` с **точными наборами**
failing/skipped ID (не счётчики). Новых FAIL быть не должно.

---

## 7. Rollback (5 уровней, от мягкого к жёсткому)

1. **Конфиг per-lane:** `toolrush: {enabled: false}` или точечно `fast_search/warm_shell/parallel_reads: false` в config.yaml владельца + свежий гейтвей.
2. **Env-гейты:** `TOOLRUSH_SEARCH=0`, `TOOLRUSH_PERSIST=0`, `TOOLRUSH_PARALLEL=0` (без рестарта не действуют — задокументировать).
3. **Список плагинов:** убрать `trix-toolrush` из `plugins.enabled` → плагин вообще не грузится.
4. **`HERMES_SAFE_MODE=1`** — все плагины мимо.
5. **Удаление:** `rm -rf <home>/plugins/trix-toolrush` — дисковое дерево Hermes не трогалось никогда, `git status` чист.

Активация — только через **свежий процесс гейтвея** при idle-сессиях (как в оригинале:
никакого хот-патча живых процессов).

---

## 8. Риски и меры

| Риск | Мера |
|---|---|
| Апстрим-дрейф после следующего `hermes update` | compat fail-closed per lane + `build_payload.py` пересборка за минуты + M9 мультиверсии |
| rg 13 без `--sortr=modified` | сохраняем существующий capability-гейт; doctor предупреждает; (опция) доставить rg ≥ 14 в venv-обвязку |
| Warm-shell выигрыш на Linux окажется мал | бенчмарки M7 до решения; «STOP» — легитимный вердикт (прецедент wave 4) |
| Snapshot-коммит race на Linux-тексте wrapper'а | rewrite выводится из живого текста `_wrap_command` и покрывается тестами commit-сьюта; при неточном матче — лейн отключается |
| 4 живых прод-гейтвея | ничего не менять в работающих процессах; включение по одному профилю, начиная со стенда |
| Требуется pytest, которого нет в venv | доставить в отдельный scratch-venv или `uv pip install` в venv (задокументировать; prod не ломается) |
| Политика апстрима по плагинам | мы не патчим диск и не переопределяем регистрации инструментов — только транспорт in-memory через официальный plugin API |

---

## 9. Трудозатраты (грубые оценки в рабочих сессиях)

| Этап | Оценка |
|---|---|
| M1 каркас + doctor | 0.5–1 день |
| M2 snapshot | 0.5 дня |
| M3 rpc + parallel() | 0.5–1 день |
| M4 files/search-транспорт (главный ручной порт) | 1–2 дня |
| M5 admission-исследование | 0.5 дня |
| M6 warm shell Linux | 1–2 дня |
| M7 бенчмарки + evidence | 0.5–1 день |
| M8 дистрибуция + runbook | 0.5 дня |
| **Итого** | **~5–9 дней**; минимальная полезная версия (M1–M3: parallel + snapshot-fixes) — ~2 дня |

---

## 10. Открытые вопросы (обсудить перед стартом)

1. ~~Имя~~ **РЕШЕНО:** `trix-toolrush` — семейство `trix-*` (прецедент: pix-ops), «toolrush» одним словом как у оригинала (атрибуция). Схема имён:
   - репо/директория/ключ: `trix-toolrush` (`plugins.enabled: [trix-toolrush]`, `<home>/plugins/trix-toolrush/`)
   - отображаемое имя: **Trix ToolRush**
   - config-секция гейтов: `trix-toolrush:` (с fallback на `toolrush:` для совместимости с upstream-документацией)
   - env-гейты: `TRIX_TOOLRUSH_*=0` первичные + `TOOLRUSH_*=0` легаси-алиасы
   - **остаётся открытым:** куда пушить репо (аккаунт GitHub; сеть через proxy, github.com в no_proxy — ок)
2. **Профиль для первой накатки:** стендовый (новый пустой профиль) → потом default? Или сразу coder?
3. **Перезапуск гейтвеев:** когда допустимо (сессии idle)? Кто даёт «гот温» — ты.
4. **rg ≥ 14:** доставить на бокс (apt/бинарник в ~/.local/bin) или жить с гейтом rg13?
5. **pytest:** ставить в прод-venv или отдельный тест-venv?
6. **Атрибуция:** LICENSE MIT + заметное указание происхождения от OnlyTerp/toolrush в README — ок?
7. **Приоритет M6 (warm shell):** делать сразу или после первого рабочего включения M2–M4?
