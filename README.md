# ModBridge

**Fully automated update pipeline for modded Minecraft servers.**

ModBridge bridges two existing tools into one unattended workflow:

- [Minecraft Server Maintainer](https://github.com/worflor/minecraft-server-maintainer) — updates Minecraft, the mod loader, and mods (via Modrinth)
- [SakuraUpdater](https://github.com/NamelessXiaoJiang/SakuraUpdater) — distributes those updates to players' clients with an in-game update GUI

Without ModBridge, every server update ends with a human typing
`sakuraupdater commit <version> <description>` into the server console.
ModBridge removes that human:

```
cron ─▶ modbridge run
          ├─ 1  preflight   lock, schedule window, sanity checks
          ├─ 2  snapshot    hash every mod jar on disk
          ├─ 3  plan        maintainer --dry-run (exit early if nothing to do)
          ├─ 4  countdown   warn players: 60s… 30s… 10s… 5 4 3 2 1
          ├─ 5  stop        graceful stop via tmux
          ├─ 6  update      maintainer --update-only --yes --no-relaunch
          ├─ 7  rescan      re-hash mods, diff against what players have
          ├─ 8  start       restart in tmux, wait for "Done ("
          ├─ 9  changelog   generate markdown (optionally Modrinth-enriched)
          ├─ 10 commit      inject `sakuraupdater commit` into the console
          ├─ 11 verify      POST /updateList must return the new version
          └─ 12 notify      Discord webhook + logs + persisted state
```

## Safety properties

- **Never publishes a broken update.** A failed maintainer run, a leftover
  `woflo/.pending` marker, a rollback, or a server that won't start all abort
  the run *before* the SakuraUpdater commit. If startup fails after an update,
  ModBridge rolls back via the maintainer and restarts the old state.
- **Never commits twice / commits nothing.** Publishing is guarded by a
  content hash of the mods directory: if what's on disk equals what players
  already received, there is no commit.
- **Never leaves the server down.** Any failure after the stop step triggers a
  recovery restart.
- **Never overlaps with itself.** A `flock`-based lock rejects concurrent runs.
- **Manual changes are first-class.** Mods you drop in by hand are detected by
  the filesystem diff and published too (after a restart, so clients never get
  files the running server hasn't loaded).

## Requirements

- Linux, Python 3.12+
- A NeoForge (or other maintainer-supported) server running inside **tmux**
- **Java 21+** for Minecraft Server Maintainer
- SakuraUpdater on the **clients** (players install it once; after that they
  receive everything through it)

The two server-side tools themselves — the Server Maintainer jar and the
SakuraUpdater server mod — do **not** need manual installation: ModBridge
downloads them from their GitHub releases automatically on the first run
(`dependencies.auto_install`), and can keep them updated too
(`dependencies.auto_update`, or manually via `modbridge setup --update`).
When SakuraUpdater itself is updated, the new jar lands in `mods/` and is
published to players like any other mod change.

SakuraUpdater's server config is managed too (`sakura.manage_config`, on by
default): declare `sakura.sync_dirs` (default `["mods:mirror"]`) in
modbridge.yaml and ModBridge writes `config/sakuraupdater-common.toml` — the
file the mod *actually* reads (its README names a `sakuraupdater-server.toml`
that is never read in mod mode) — during a window where the server is down.
Commit verification also fails loudly if a published manifest contains zero
files, which is the symptom of a broken `SYNC_DIR`.

## Install

ModBridge is not published to PyPI; install it straight from this repository.
On modern distros (PEP 668 "externally-managed-environment"), use pipx:

```bash
apt install pipx              # if you don't have it yet
pipx install git+https://github.com/gvoze32/modbridge.git
```

If `modbridge` is not found afterwards (pipx installs to `~/.local/bin`), run
`pipx ensurepath` once and open a new shell. Cron doesn't read your shell PATH
either way — use the absolute path (e.g. `~/.local/bin/modbridge`) in crontab.

Or with a plain virtualenv:

```bash
python3 -m venv /opt/modbridge
/opt/modbridge/bin/pip install git+https://github.com/gvoze32/modbridge.git
ln -s /opt/modbridge/bin/modbridge /usr/local/bin/modbridge
```

To upgrade later, re-run the install command (with pipx, add `--force`:
`pipx install --force git+https://github.com/gvoze32/modbridge.git`).
Then configure:

```bash
cd /home/mc/server
curl -fsSLO https://raw.githubusercontent.com/gvoze32/modbridge/main/config.example.yaml
cp config.example.yaml modbridge.yaml
$EDITOR modbridge.yaml
modbridge validate
```

Run ModBridge as the same user that owns the tmux session — `tmux send-keys`
can only reach sessions belonging to the invoking user.

## Usage

```bash
modbridge run                 # what cron calls; honors the schedule window
modbridge run --force         # admin: update NOW, bypass the window
modbridge run --no-countdown  # skip the player warning
modbridge dry-run             # preview planned updates, change nothing
modbridge status              # last run, last published version, pending changes
modbridge validate            # check config + referenced paths
modbridge setup               # download maintainer jar + SakuraUpdater mod
modbridge setup --update      # also swap in newer upstream releases
```

Cron example (checks every 30 min; the `schedule.window` in the config decides
when updates actually happen):

```cron
*/30 * * * * cd /home/mc/server && /home/mc/.local/bin/modbridge run -c modbridge.yaml >> .modbridge/cron.log 2>&1
```

## Configuration

See [config.example.yaml](config.example.yaml) — every key is documented there.
Two settings deserve special attention:

- `maintainer.accept_eula: true` is **required**: unattended updates run the
  maintainer with `--yes`, which accepts the
  [Minecraft EULA](https://aka.ms/MinecraftEULA) on your behalf.
- `schedule.window: "04:00-05:00"` restricts restarts to a maintenance window;
  overnight windows (`"22:00-02:00"`) work too.

## How it integrates (for the curious)

- The maintainer is driven as a subprocess with
  `--update-only --yes --no-relaunch` and `NO_COLOR=1`; ModBridge parses
  `woflo/update.log` for `Update | name old -> new` lines, checks for the
  `woflo/.pending` crash marker, and reads `current_version.txt`.
  The maintainer's own crash-loop supervisor is deliberately not used — tmux
  stays in charge of the server process.
- The SakuraUpdater commit is injected into the server console via
  `tmux send-keys`. The changelog is written to a file and its *path* is passed
  as the commit description — SakuraUpdater then embeds the file's markdown
  content. The commit is verified through SakuraUpdater's embedded HTTP API
  (`POST /updateList`), not by log scraping.
- Ground truth for "did anything change" is ModBridge's own SHA-256 manifest of
  `mods/`, with mod names/versions read from each jar's metadata
  (`neoforge.mods.toml` / `mods.toml` / `fabric.mod.json` / `quilt.mod.json`).

## Architecture

Clean, adapter-based layout — each external system sits behind a `Protocol`, so
new backends (Packwiz, RCON, systemd, other distributors) are drop-in plugins:

```
src/modbridge/
├── domain/       manifests, change sets, diffing (pure, no I/O)
├── mods/         mods-directory scanner (jar metadata + hashing)
├── config/       pydantic-validated YAML config
├── schedule.py   update window
├── state/        atomic state store, run journal, flock lock
├── adapters/
│   ├── base.py       UpdaterBackend / ServerSupervisor / Distributor / NotificationSink
│   ├── maintainer.py Minecraft Server Maintainer (subprocess)
│   ├── tmux.py       tmux supervisor + rotation-aware log watcher
│   ├── sakura.py     SakuraUpdater (console inject + HTTP verify)
│   └── notify.py     Discord webhook, log sink
├── changelog/    Jinja2 markdown renderer + optional Modrinth enrichment
├── pipeline/     step functions + engine (journaled, self-recovering)
└── cli/          typer CLI
```

## Development

```bash
pip install -e ".[dev]"
ruff check src tests && mypy && pytest
```

The pipeline is fully testable without a real server: all adapters have
in-memory fakes (`tests/fakes.py`), and the test suite covers the happy path,
failed updates, rollback-on-broken-startup, manual-change publishing, schedule
windows, dry runs, and crash recovery.

## License

MIT. Upstream projects keep their own licenses (Server Maintainer: GPL-3.0;
SakuraUpdater: all rights reserved) — ModBridge only orchestrates them as
external processes and does not redistribute either.
