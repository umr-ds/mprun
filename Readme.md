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
mprun_client [list|get|create] # (package entrypoint)
```

See `mprun_client --help` for all options.

## Experiment creation

The client can create an experiment from a TOML file.
The file format is as follows:

```toml
name = "test experiment"

[params]
foo = [1, 2, 3]
bar = [true, false]
```

- `name` is the human-readable experiment name. This name does not need to be unique, as each experiment will have its own unique ID.
- `params` can have an arbitrary number of parameters. Each parameter must be a list of arbitrary values.

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
