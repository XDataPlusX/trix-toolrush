# Trix ToolRush — Validation Evidence (v0.4.0, 2026-09-06)

Methodology inherited from ToolRush v2: paired on/off measurement, negative
controls, real entry points, honest-stop verdicts. Dev box: Linux, hermes
2b55ded1ac (v0.21.0), Python 3.11.15, rg 13.0.0.

## Test suite

- **78 tests green, 0 failed** (9 skeleton + 12 rpc + 11 snapshot + 30 files
  + 11 warm + 5 misc), run against the LIVE tree with lanes installed
  in-memory only; Hermes venv untouched (scratch venv + PYTHONPATH).
- Negative control: `TRIX_TOOLRUSH_PARALLEL=0` fails the overlap test with
  the intended reason ("Trix ToolRush parallel reads are disabled").
- Hash discipline: any helper edit requires a payload rebuild — enforced by
  sha256 verification (observed live during development).

## Benchmarks (paired, medians)

### Search transport (files lane)
| workload | shell | native | speedup |
|---|---:|---:|---:|
| content search, real tree, limit 30 | 9.7 ms | 9.5 ms | **1.01x** |
| no-match + 3 diagnostics probes | 24.7 ms | 24.6 ms | **1.00x** |
| file discovery | 17.5 ms | 17.1 ms | **1.02x** |

Native path proven real (no-shell assertion). Verdict: **warm-Linux transport
tax ≈ 0**; the original 5–7x was Windows spawn tax. Lane ships for
correctness (strict-JSON `_hint`, `--sort=path` stable pagination, honest
`total_count_is_lower_bound`, unterminated-final-line count, memoized guard).

### Terminal transport (warm shell)
| workload | stock spawn | warm broker | speedup |
|---|---:|---:|---:|
| printf builtin | 7.1 ms | 6.1 ms | **1.17x** |
| git rev-parse (real repo) | 13.7 ms | 13.7 ms | **1.00x** |
| python3 --version | 13.7 ms | 13.7 ms | **1.00x** |

Verdict: **honest-stop**. On warm Linux a bash spawn costs ~2–3 ms; the
original 23.6x was Windows spawn tax. The lane is shipped and fully tested
(streaming, atomic synchronous snapshot commit via os.replace, killpg tree
kill, background bypass, busy fallback, timeout reap+recover) but is
**opt-in**: enable `warm_shell: true` only on hosts with expensive spawns
(containers, cold caches, network filesystems).

### RPC batches (ported from upstream evidence)
Upstream measured 2.06x on four searches, 1.44x mixed, 0.98x tiny reads
(disclosed regression), 3.34x controlled overlap. Not re-measured here (same
code path, same executor); doctor --smoke exercises a real 2-read batch
through execute_code on every run.

## Lane verdicts

| lane | status | verdict |
|---|---|---|
| snapshot | shipped, ON | safety fail-closed fixes; ported from upstream + getattr self-sufficiency fix (upstream payload had a latent class-default omission) |
| rpc | shipped, ON | parallel() batches through real kernel; doctor-smoked |
| files | shipped, ON | correctness-focused; native transport for expensive-spawn hosts |
| shell | shipped, opt-in | honest-stop on warm Linux; for containers |
| admission | **closed: obsolete** | upstream removed `_is_readonly_terminal_command` (2026-08-25); terminal is a name-gated sequential barrier (`_PARALLEL_SAFE_TOOLS`, agent/tool_dispatch_helpers.py:48–61, 268; pinned by tests/run_agent/test_tool_batch_segmentation.py:552–558). No call site exists for a readonly classifier. |

## Docker findings (M8)

- User fleet: gateway runs locally; `nikolaik/python-nodejs:python3.11`
  workhorse containers serve as terminal backends (e.g. pix). Lanes patching
  `LocalEnvironment` are **inert on docker backends by design** (native
  context returns None → stock path; fail-closed).
- Official Hermes image (debian trixie, /opt/hermes, Python 3.13):
  `payload-313.json` shipped and selected automatically by runtime Python.
  Plugins volume is shared with the host (`~/.hermes:/opt/data`), rg present
  in the image. In-image smoke pending an actual image deployment (not
  present on this host; documented in docs/DOCKER.md).

## Update survival

`scripts/rebuild_payload.sh` regenerates the payload from any hermes-agent
checkout (baseline ref selectable); per-Python payloads (`payload.json` for
3.11, `payload-313.json`) are selected by the compat loader. Unknown upstream
drift degrades the affected lane loudly (`ToolRush ... disabled on changed
upstream` warning) and the gateway continues on stock transports.
