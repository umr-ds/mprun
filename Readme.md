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

```bash
mprun_server [--host HOST] [--port PORT] [-v] # (package entrypoint)
```

Default: `127.0.0.1:8000`. Configured via environment variables:

| Variable          | Effect                                            | Default     |
|:------------------|:--------------------------------------------------|:------------|
| `MPRUN_DATA_PATH` | Directory for database / blob storage             | `~/.mprun`  |

### Worker

```bash
mprun_worker [-v] # (package entrypoint)
```

Configured via environment variables (all required):

| Variable                 | Effect                              |
|:-------------------------|:------------------------------------|
| `MPRUN_SERVER_ADDRESS`   | Address of the server               |
| `MPRUN_WORKER_NAME`      | Human-readable name for this worker |
| `MPRUN_WORKER_DIRECTORY` | Working directory for run execution |

### Client

```bash
mprun_client [COMMAND] [OPTIONS]
```

Run without a subcommand to open the interactive TUI. Pass a subcommand for non-interactive use.

#### CLI subcommands

| Command                                          | Description                                    |
|:-------------------------------------------------|:-----------------------------------------------|
| `list [-u URL] [-j]`                             | List all experiments                           |
| `get <eid> [-u URL] [-j]`                        | Show experiment and its runs                   |
| `create <file.toml> [-u URL] [-j]`               | Submit experiment from TOML file               |
| `delete <eid> [-u URL] [-y]`                     | Delete experiment (prompts unless `-y`)        |
| `results <eid>-<index>-<iter> [-o DIR] [-u URL]` | Download single run's result archive           |
| `get-results <eid> [-o DIR] [-u URL]`            | Download all result archives for an experiment |

`-u` / `--base-url` overrides `MPRUN_SERVER_ADDRESS` (default `http://localhost:8000`).
`-j` / `--json` prints raw JSON instead of a table.

#### Interactive TUI

```bash
mprun_client          # opens TUI
```

The TUI has three screens: **Over-View**, **Detail-View**, and **Create-Mode**.

##### Over-View (experiment list)

| Key       | Action                                                           |
|:----------|:-----------------------------------------------------------------|
| `↑` / `↓` | Navigate experiments                                             |
| `Enter`   | Open Detail-View for selected experiment                         |
| `f`       | Enter search mode (fuzzy-match by name; `Escape` to exit)        |
| `d`       | Download all results for selected experiment into `<cwd>/<eid>/` |
| `r`       | Delete selected experiment (confirmation required)               |
| `c`       | Switch to Create-Mode                                            |
| `Ctrl+R`  | Refresh list now (auto-refreshes every 30 s)                     |
| `q`       | Quit                                                             |

##### Detail-View (runs of one experiment)

| Key       | Action                                                      |
|:----------|:------------------------------------------------------------|
| `↑` / `↓` | Navigate runs                                               |
| `d`       | Download selected run's result archive to current directory |
| `r`       | Refresh now                                                 |
| `Escape`  | Back to Over-View                                           |

##### Create-Mode (three-pane file explorer)

| Key           | Action                                               |
|:--------------|:-----------------------------------------------------|
| `↑` / `↓`     | Navigate entries                                     |
| `→`           | Enter highlighted directory                          |
| `←`           | Go to parent directory                               |
| `Enter` / `c` | Preview selected `.toml` file and confirm submission |
| `y` / `n`     | Confirm or cancel submission in the preview dialogue |
| `v`           | Switch back to Over-View                             |
| `q`           | Quit                                                 |

Non-TOML files and directories are dimmed; only `.toml` files can be submitted.

## Experiment creation

Experiments are defined in TOML files and submitted via `mprun_client create <file.toml>` or the TUI's Create-Mode.

The TOML file must live in the same directory as the `executable` (and `setup_executable`, if used). All paths in `environment_files` are resolved relative to that directory.

### Minimal example

```toml
name = "my experiment"
executable = "run.sh"

[params]
learning_rate = [0.001, 0.01, 0.1]
batch_size    = [32, 64]

[results]
"output/metrics.json" = "metrics.json"
```

This produces 6 runs (3 × 2 Cartesian product). Each run receives its parameter combination as command-line arguments.

### Full example

```toml
name        = "grid search"
executable  = "train.py"
setup_executable = "setup.sh"   # optional: run once before each main run
iterations  = 3                 # repeat each param combo 3 times
timeout     = 3600              # seconds; omit for no timeout

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

| Field                   | Required | Description                                                                                                                                           |
|:------------------------|:--------:|:------------------------------------------------------------------------------------------------------------------------------------------------------|
| `name`                  |   yes    | Human-readable label. Need not be unique — each experiment gets its own UUID.                                                                         |
| `executable`            |   yes    | Filename of the main script/binary. Must exist in the same directory as the TOML and be marked executable.                                            |
| `params`                |   yes    | Dict of `param_name = [value, …]`. Values can be `str`, `int`, `float`, or `bool`. Runs = Cartesian product of all lists.                             |
| `results`               |   yes    | Dict of `"worker_path" = "archive_name"`. Paths/directories collected from the worker after each run and stored in the result archive.                |
| `iterations`            |    no    | How many times each parameter combination is run. Default `1`. Use `>1` for non-deterministic experiments.                                            |
| `timeout`               |    no    | Per-run timeout in seconds. Omit (or set to nothing) for unlimited.                                                                                   |
| `setup_executable`      |    no    | Script run before each main run. Must be in the same directory as the TOML and be marked executable.                                                  |
| `environment_variables` |    no    | Dict of `NAME = "value"` pairs set in the worker's environment before execution.                                                                      |
| `environment_files`     |    no    | Dict of `"src" = "dst"`. Source paths are relative to the TOML directory; destinations are paths on the worker. Files and directories both supported. |

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
