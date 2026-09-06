# Trix ToolRush

**Status: M1 skeleton (work in progress).** Compatibility loader, runtime gates,
doctor and payload builder are in place; acceleration lanes land milestone by
milestone (see `docs/PLAN.md`). Nothing accelerates yet — the skeleton payload
ships zero lanes and is safe to install for bootstrap verification.

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

Rebuild the compatibility payload after editing patched sources in a working
copy of hermes-agent (build under the target runtime's Python minor):

```bash
python build_payload.py --root /path/to/patched-hermes-agent --baseline <upstream-ref>
```

Tests: `python -m pytest tests/ -q` (needs a Hermes checkout for lane tests;
skeleton tests run standalone).

Roadmap and full methodology: `docs/PLAN.md`. Docker notes: `docs/DOCKER.md`.

## Attribution

Linux port of [ToolRush v2](https://github.com/OnlyTerp/toolrush) by OnlyTerp
(MIT) — the compatibility-loader design, lane architecture, validation
methodology and tests are derived from the original. Trix ToolRush re-targets
the transports to Linux/Docker and rebuilds the payload against the live
Hermes upstream.

## License

MIT — see `LICENSE`.
