# Trix ToolRush

**Status: v0.4.0 — all lanes shipped.** Lanes live: **snapshot** (4 fail-closed
safety patches), **rpc** (`parallel()` batched reads through the real
`execute_code` kernel — verified by doctor smoke) and **files** (12 patches:
native rg transport + strict-JSON envelopes, stable pagination, guard
memoization) and **shell** (streaming persistent-bash transport with
synchronous atomic snapshot commit — opt-in: on warm Linux the measured gain
is 1.0–1.17x, so `warm_shell: true` is recommended only for hosts with
expensive spawns, e.g. containers). Full roadmap: `docs/PLAN.md`.

Measured on warm Linux (dev box, paired on/off, medians): search transport
9.7→9.5 ms (**1.01x**), no-match 24.7→24.6 ms (**1.00x**), discovery
17.5→17.1 ms (**1.02x**) — the original ToolRush 5–7x wins were a Windows
spawn tax; on Linux the transport is already cheap, so the files lane ships
for **correctness** (strict JSON `_hint`, `--sort=path` stable pagination,
honest `total_count_is_lower_bound`, unterminated-final-line count, memoized
read-block guard) and for hosts with expensive spawns. Honest-stop verdict
recorded in `docs/PLAN.md`.

Low-overhead execution layer for [Hermes Agent](https://github.com/NousResearch/hermes-agent)
on **Linux and Docker**, for the Trix bot fleet. Linux port of
[ToolRush v2](https://github.com/OnlyTerp/toolrush) (MIT, attribution below):
same tools, same output envelopes, same safety gates — radically cheaper
transports.

- **native rg search transport** — ripgrep runs as a direct argv process, no
  bash round-trip per search, real ignore files and regex grammar preserved
- **batched parallel read RPC** — `from hermes_tools import parallel([...])`,
  1–16 read calls, ≤4 workers, input order, budgets and allowlists enforced
- **streaming warm shell** — one persistent bash broker, synchronous atomic
  snapshot commit, full command-tree cancellation

Everything is **in-memory**: function-level, hash-verified compatibility
patches restore themselves over known upstream states and refuse loudly on
unknown drift. The Hermes checkout on disk is never modified — `git status`
stays clean forever.

## Install

Local Hermes (Linux):

```bash
hermes plugins install https://github.com/XDataPlusX/trix-toolrush.git --enable
```

or manually:

```bash
git clone https://github.com/XDataPlusX/trix-toolrush.git ~/.hermes/plugins/trix-toolrush
hermes plugins enable trix-toolrush
```

Per profile: run with `-p <profile>` / set `HERMES_HOME` — each profile is an
isolated home with its own `plugins/` and `config.yaml`.

Docker: `~/.hermes` is volume-mounted to `/opt/data` (`HERMES_HOME`), so the
same `plugins/trix-toolrush` directory serves container gateways too. The
official image runs Python 3.13 vs 3.11 on a local venv, so per-Python payload
variants are selected automatically (`payload-311.json` / `payload-313.json`);
a lane refuses to load if no payload matches the runtime Python (fail-closed).
See `docs/DOCKER.md`.

## Configuration

Owning profile's `config.yaml`:

```yaml
plugins:
  enabled: [trix-toolrush]
trix-toolrush:
  enabled: true        # master switch
  fast_search: true
  parallel_reads: true
  warm_shell: true
  fast_read: true
```

Activation requires a **fresh gateway process** (never hot-patched). Verify:

```bash
python ~/.hermes/plugins/trix-toolrush/doctor.py            # integrity + lane compatibility
python ~/.hermes/plugins/trix-toolrush/doctor.py --smoke    # + real PluginManager boot
```

## Rollback (softest first)

1. Config lane off: `trix-toolrush: {fast_search: false}` (or `enabled: false`) + gateway restart
2. Env kill-switches: `TRIX_TOOLRUSH_SEARCH=0`, `TRIX_TOOLRUSH_PARALLEL=0`, `TRIX_TOOLRUSH_PERSIST=0` (upstream aliases `TOOLRUSH_*=0` honored)
3. Remove from `plugins.enabled` — plugin never loads
4. `HERMES_SAFE_MODE=1` — all plugins skipped
5. Delete `plugins/trix-toolrush/` — the Hermes tree was never touched on disk

## Development

Rebuild the compatibility payload (stages upstream ToolRush lane rows into a
disposable clone with Trix renames, then diffs against the git baseline):

```bash
scripts/rebuild_payload.sh                       # defaults: ~/.hermes/hermes-agent, HEAD
TRIX_BUILD_PYTHON=... scripts/rebuild_payload.sh # or another target python
```

Tests (need a Hermes checkout; deps come from its venv, nothing is installed
into it):

```bash
uv venv --python <hermes>/venv/bin/python --system-site-packages /tmp/trix-test
uv pip install --python /tmp/trix-test/bin/python pytest
PYTHONPATH=<hermes>/venv/lib/python3.11/site-packages /tmp/trix-test/bin/python -m pytest tests/ -q
```

Port note: the upstream ToolRush payload references a class-level
`_snapshot_exclusion_broken = False` default that its own payload never
carries (their builder extracts functions only) — it only worked on trees
patched on disk. Trix ToolRush's snapshot lane reads the latch defensively
(`getattr(..., False)`) so the in-memory payload is self-sufficient. Found by
differential testing; regression-covered by the snapshot lane tests.

Roadmap and full methodology: `docs/PLAN.md`. Docker notes: `docs/DOCKER.md`.

## Attribution

Linux port of [ToolRush v2](https://github.com/OnlyTerp/toolrush) by OnlyTerp
(MIT) — the compatibility-loader design, lane architecture, validation
methodology and tests are derived from the original. Trix ToolRush re-targets
the transports to Linux/Docker and rebuilds the payload against the live
Hermes upstream.

## License

MIT — see `LICENSE`.
