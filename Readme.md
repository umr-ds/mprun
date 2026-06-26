# MPRun - Multi-Parameter Experiment Runner

A system for orchestrating/running experiments with arbitrary parameter-sets.
Will scan the entire parameter-space.

## Installation

Install the base package plus the dependency group for your role (`server`, `worker`, or `client`):

```bash
# with uv
uv sync --group server   # or --group worker / --group client

# with pip
pip install --group server .   # or --group worker / --group client
```

## Running the program

### Server

The server reads all settings from a TOML config file.

```bash
mprun_server [-c CONFIG]
```

| CLI argument    | Description                                                  |
|:----------------|:-------------------------------------------------------------|
| `-c`/`--config` | Path to TOML config file. Defaults to platform config dirs.  |

Config file locations (checked in order):

- `$XDG_CONFIG_HOME/mprun/server.toml` (`~/.config/mprun/server.toml`)
- `/etc/xdg/mprun/server.toml` (site-wide, per platformdirs)

**Config fields:**

| Field            | Required | Description                                                                         |
|:-----------------|---------:|:------------------------------------------------------------------------------------|
| `host`           |      yes | Bind address (e.g. `"127.0.0.1"`, `"0.0.0.0"`)                                      |
| `port`           |      yes | Bind port (e.g. `8000`)                                                             |
| `home_directory` |       no | Directory for database and blob storage (defaults to `$XDG_DATA_HOME/mprun/server`) |
| `log_level`      |       no | `"DEBUG"`, `"INFO"` (default), `"WARNING"`, `"ERROR"`, `"CRITICAL"`                 |

Example:

```toml
host = "127.0.0.1"
port = 8000
home_directory = "/var/lib/mprun/server"
log_level = "INFO"
```

### Worker

The worker reads all settings from a TOML config file.

```bash
mprun_worker [-c CONFIG]
```

| CLI argument    | Description                                                  |
|:----------------|:-------------------------------------------------------------|
| `-c`/`--config` | Path to TOML config file. Defaults to platform config dirs.  |

Config file locations (checked in order):

- `$XDG_CONFIG_HOME/mprun/worker.toml` (`~/.config/mprun/worker.toml`)
- `/etc/xdg/mprun/worker.toml` (site-wide, per platformdirs)

**Config fields:**

| Field              | Required | Description                                                                                                           |
|:-------------------|---------:|:----------------------------------------------------------------------------------------------------------------------|
| `server_address`   |      yes | Address of the server (e.g. `"localhost:8000"`)                                                                       |
| `name`             |      yes | Human-readable name for this worker                                                                                   |
| `backends`         |      yes | Backends this worker supports. One or more of `"NATIVE"`, `"DOCKER"`. Runs are only dispatched to matching backends.  |
| `home_directory`   |       no | Working directory for run execution (defaults to `$XDG_DATA_HOME/mprun/worker`)                                       |
| `docker_backend`   |       no | Configuration for the Docker execution backend. Only needed when `"DOCKER"` is in `backends`.                         |
| `log_level`        |       no | `"DEBUG"`, `"INFO"` (default), `"WARNING"`, `"ERROR"`, `"CRITICAL"`                                                   |

**`docker_backend` fields:**

| Field       | Required | Description                                                                         |
|:------------|---------:|:------------------------------------------------------------------------------------|
| `base_url`  |       no | URL or UNIX socket for the Docker daemon (default: `"unix:///var/run/docker.sock"`) |

Example — a worker that supports both native and Docker execution:

```toml
server_address = "localhost:8000"
name = "gpu-node-1"
backends = ["NATIVE", "DOCKER"]
home_directory = "/var/lib/mprun/worker"
log_level = "INFO"

[docker_backend]
base_url = "unix:///var/run/docker.sock"
```

### Client

The client reads settings from a TOML config file. All settings can be overridden via CLI arguments.

```bash
mprun_client [-u URL] [-c CONFIG] [COMMAND] [OPTIONS]
```

Run without a subcommand to open the interactive TUI. Pass a subcommand for non-interactive use.

Config file locations (checked in order):

- `$XDG_CONFIG_HOME/mprun/client.toml` (`~/.config/mprun/client.toml`)
- `/etc/xdg/mprun/client.toml` (site-wide, per platformdirs)

**Config fields:**

| Field            | Required | Description                                     |
|:-----------------|---------:|:------------------------------------------------|
| `server_address` |       no | Address of the server (e.g. `"localhost:8000"`) |

Example:

```toml
server_address = "localhost:8000"
```

**Server address resolution order** (first wins):

1. `-u` / `--base-url` CLI argument
2. `server_address` in config file
3. `MPRUN_SERVER_ADDRESS` environment variable
4. `http://localhost:8000` (default)

#### CLI subcommands

| Command                                  | Description                                    |
|:-----------------------------------------|:-----------------------------------------------|
| `list [-j]`                              | List all experiments                           |
| `get <eid> [-j]`                         | Show experiment and its runs                   |
| `create <file.toml> [-j]`                | Submit experiment from TOML file               |
| `delete <eid> [-y]`                      | Delete experiment (prompts unless `-y`)        |
| `purge`                                  | Purge all dead workers (cannot be undone)      |
| `workers [-j]`                           | List all registered workers                    |
| `results <eid>-<index>-<iter> [-o DIR]`  | Download single run's result archive           |
| `reset <eid>-<index>-<iter>`             | Reset a run (delete results, set to WAITING)   |
| `get-results <eid> [-o DIR]`             | Download all result archives for an experiment |

`-j` / `--json` prints raw JSON instead of a table.

#### Interactive TUI

```bash
mprun_client          # opens TUI
```

The TUI has four screens: **Over-View**, **Detail-View**, **Create-Mode**, and **Worker-View**.

##### Over-View (experiment list)

| Key          | Action                                                           |
|:-------------|:-----------------------------------------------------------------|
| `↑` / `↓`    | Navigate experiments                                             |
| `Enter`      | Open Detail-View for selected experiment                         |
| `f`          | Enter search mode (fuzzy-match by name; `Escape` to exit)        |
| `d`          | Download all results for selected experiment into `<cwd>/<eid>/` |
| `Backspace`  | Delete selected experiment (confirmation required)               |
| `c`          | Switch to Create-Mode                                            |
| `w`          | Switch to Worker-View                                            |
| `Ctrl+R`     | Refresh list now (auto-refreshes every 30 s)                     |
| `q`          | Quit                                                             |

##### Detail-View (runs of one experiment)

| Key        | Action                                                      |
|:-----------|:------------------------------------------------------------|
| `↑` / `↓`  | Navigate runs                                               |
| `d`        | Download selected run's result archive to current directory |
| `r`        | Reset selected run (delete results, return to WAITING)      |
| `Ctrl+R`   | Refresh now                                                 |
| `Escape`   | Back to Over-View                                           |

##### Create-Mode (three-pane file explorer)

| Key           | Action                                               |
|:--------------|:-----------------------------------------------------|
| `↑` / `↓`     | Navigate entries                                     |
| `→`           | Enter highlighted directory                          |
| `←`           | Go to parent directory                               |
| `Enter` / `c` | Preview selected `.toml` file and confirm submission |
| `y` / `n`     | Confirm or cancel submission in the preview dialogue |
| `v`           | Switch to Over-View                                  |
| `w`           | Switch to Worker-View                                |
| `q`           | Quit                                                 |

Non-TOML files and directories are dimmed; only `.toml` files can be submitted.

##### Worker-View (registered workers list)

| Key        | Action                                                       |
|:-----------|:-------------------------------------------------------------|
| `↑` / `↓`  | Navigate workers                                             |
| `g`        | Toggle grouping by worker state                              |
| `p`        | Purge all dead workers (confirmation required)               |
| `v`        | Switch to Over-View                                          |
| `c`        | Switch to Create-Mode                                        |
| `Ctrl+R`   | Refresh now                                                  |

The detail panel shows each worker's name, WID, backend, state, join time, last check-in, and current run.

## Experiment backends

Backends control *where* and *how* experiment runs are executed. Each experiment declares which backends it supports via the `backends` field in its TOML definition. Workers declare which backends they offer in their config. The server only dispatches runs to workers whose backends overlap with the experiment's requirements.

### NATIVE

Runs the experiment directly on the worker's host system without sandboxing.
The worker unpacks the experiment archive, sets up environment variables and files, then executes the main script natively.

Use this when:
- The worker host already has all required dependencies.
- No isolation between runs is needed.
- You want the simplest possible setup.

### DOCKER

Builds a Docker image from the experiment's `Dockerfile`, then runs the executable inside a container.
The experiment directory is volume-mounted into the container at `/workspace`.

The **`Dockerfile`** is responsible for:
- Installing dependencies (Python packages, system libraries, etc.).
- Copying environment files (from `[environment_files]`) to their configured destinations.

The `Dockerfile` must be in the same directory as the experiment's TOML file.
When the `DOCKER` backend is declared, the `Dockerfile` is automatically included in the experiment archive and validated on submission.

Use this when:
- Experiments need isolated environments.
- Different experiments require conflicting dependency versions.
- The worker host should not be modified by experiment code.

### Dispatch logic

When a worker polls for work, the server finds the first waiting run whose `backends` intersect with the worker's declared backends.
If the worker supports both `NATIVE` and `DOCKER`, the Docker backend is preferred.

## Experiment creation

Experiments are defined in TOML files and submitted via `mprun_client create <file.toml>` or the TUI's Create-Mode.

The TOML file must live in the same directory as the `executable` (and `setup_executable`, if used).
All paths in `environment_files` are resolved relative to that directory. If using the `DOCKER` backend, a `Dockerfile` must also be present in the same directory.

### Minimal example

```toml
name = "my experiment"
executable = "run.sh"
backends = ["NATIVE"]

[params]
learning_rate = [0.001, 0.01, 0.1]
batch_size    = [32, 64]

[results]
"output/metrics.json" = "metrics.json"
```

This produces 6 runs (3 × 2 Cartesian product).
Each run receives its parameter combination as command-line arguments.

### Full example

```toml
name        = "grid search"
executable  = "train.py"
setup_executable = "setup.sh"   # optional: run once before each main run
iterations  = 3                 # repeat each param combo 3 times
timeout     = 3600              # seconds; omit for no timeout
backends    = ["NATIVE", "DOCKER"]

[params]
learning_rate = [0.001, 0.01]
dropout       = [0.1, 0.5]
seed          = [42]

[results]
# worker_path = name_inside_results_archive
"output/model.pt"      = "model.pt"
"output/metrics.json"  = "metrics.json"

[environment_variables]
CUDA_VISIBLE_DEVICES = "0"
LOG_LEVEL            = "INFO"

[environment_files]
# source (relative to TOML dir) = destination on worker
"data/train.csv" = "data/train.csv"
"configs/"       = "configs/"
```

Total runs = `product(len(values) for each param) × iterations` = 2 × 2 × 1 × 3 = 12.

### Field reference

| Field                   | Required | Description                                                                                                                                                            |
|:------------------------|:--------:|:-----------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `name`                  |   yes    | Human-readable label. Need not be unique — each experiment gets its own UUID.                                                                                          |
| `executable`            |   yes    | Filename of the main script/binary. Must exist in the same directory as the TOML and be marked executable.                                                             |
| `params`                |   yes    | Dict of `param_name = [value, …]`. Values can be `str`, `int`, `float`, or `bool`. Runs = Cartesian product of all lists.                                              |
| `backends`              |   yes    | List of worker backends this experiment may run on. One or more of `"NATIVE"`, `"DOCKER"`. When `DOCKER` is included, a `Dockerfile` must exist in the same directory. |
| `results`               |   yes    | Dict of `"worker_path" = "archive_name"`. Paths/directories collected from the worker after each run and stored in the result archive.                                 |
| `iterations`            |    no    | How many times each parameter combination is run. Default `1`. Use `>1` for non-deterministic experiments.                                                             |
| `timeout`               |    no    | Per-run timeout in seconds. Omit (or set to nothing) for unlimited.                                                                                                    |
| `setup_executable`      |    no    | Script run before each main run. Must be in the same directory as the TOML and be marked executable.                                                                   |
| `environment_variables` |    no    | Dict of `NAME = "value"` pairs set in the worker's environment before execution.                                                                                       |
| `environment_files`     |    no    | Dict of `"src" = "dst"`. Source paths are relative to the TOML directory; destinations are paths on the worker. Files and directories both supported.                  |

### Examples

Complete working examples are available in the `examples/` directory:

- [`native_experiment`](examples/native_experiment/Readme.md) — runs on the host without sandboxing.
- [`docker_experiment`](examples/docker_experiment/Readme.md) — runs inside a Docker container.

## Development

While you are, of course, free to use whatever toolchain you want, we recommend [uv](https://docs.astral.sh/uv/).

After checking out the project, run 

```bash
uv sync --all-groups   # creates .venv — prefix commands with `uv run`, don't use .venv directly
pre-commit install     # install hooks (runs ruff + ty + vermin before each commit)
uv run pytest          # tests
uv run ruff check      # lint
uv run ruff format     # format
uv run ty check        # type check (Astral `ty`)
uv run vermin .        # verify Python >= 3.12
```

The CI-pipeline runs all the same checks, so check before committing.
The CI-pipeline *also* runs all the tests, so make sure those pass as well.
