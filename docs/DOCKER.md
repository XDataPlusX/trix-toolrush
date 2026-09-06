# Trix ToolRush — Docker notes

Findings from the official Hermes Agent image (`Dockerfile`, `docker-compose.yml`,
checked 2026-09-06):

| Aspect | Local Linux | Docker |
|---|---|---|
| Code checkout | `~/.hermes/hermes-agent` | `/opt/hermes` (baked into image) |
| `HERMES_HOME` (data) | `~/.hermes` | `/opt/data` = volume `~/.hermes:/opt/data` |
| Plugins dir | `~/.hermes/plugins` | `/opt/data/plugins` — **same host directory** |
| ripgrep | `/usr/bin/rg` 13.0.0 (host) | **installed in image** (apt `ripgrep`) |
| Python | venv 3.11.15 | **3.13** (uv-python3.13 base, debian trixie) |
| bash | /usr/bin/bash 5.1 | bash present (debian base) |

## Implications

1. **One plugin directory serves both backends** — the plugins volume is shared.
   Installing into `~/.hermes/plugins/trix-toolrush` makes it visible to Docker
   gateways immediately (after their restart).
2. **Python mismatch is real**: payload bytecode fingerprints are per-minor.
   Trix ToolRush selects `payload-311.json` / `payload-313.json` by runtime
   automatically; a missing variant means that backend's lanes refuse to load
   (fail-closed, gateway keeps working on stock transports). Rebuild variants
   with `build_payload.py` under each target Python.
3. **Doctor inside the container**:
   ```bash
   docker exec <container> python /opt/data/plugins/trix-toolrush/doctor.py --root /opt/hermes
   ```
   (or rely on the built-in `/opt/hermes` candidate). On the host, pass
   `--root ~/.hermes/hermes-agent` or set `TRIX_HERMES_ROOT`.
4. **rg capability**: `order="modified"` needs rg ≥ 14 (the capability gate in
   Hermes already errors clearly on rg 13). The image's rg version depends on
   the Debian release — keep the capability check intact, never assume.
5. **Gateway restarts in Docker**: `docker compose restart` (or the platform's
   supervisor) — same rule as local: only restart when sessions are idle;
   activation is never a hot-patch.
6. **HERMES_UID**: the compose file maps host uid via `HERMES_UID` env — plugin
   files written by the host user stay readable in-container.

## Open items (M8)

- [ ] Build and ship a `payload-313.json` variant (build inside the image or
      with a 3.13 interpreter).
- [ ] Verify warm-shell lane behavior with the container's process-tree
      killing (`killpg` availability, `start_new_session` semantics).
- [ ] Confirm plugins discovery inside the container sees `/opt/data/plugins`
      with the same precedence as local.
