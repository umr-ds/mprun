# MPRun - Multi-Parameter Task Runner

A system for orchestratin/running jobs with arbitrary parameter-sets.
Will scan the entire parameter-space.

## Installation

Install project & common dependencies via `pip`

```bash
pip install -e .
```
then, depending on which role (`server`, `worker`, `client`) you want to run, install the corresponding dependency group:

```bash
pip install --group server .
pip install --group worker .
pip install --group client .
```

For development, install the `devel` dependencies:

```bash
pip install --group devel .
```

and initialise `pre-commit`

```bash
pre-commit
```

## Running the program

### Server

The server can be run via

```bash
uvicorn mprun.server:app
```

the server is configured via environment variables:

| Variable               | Effect                                                               |
|:-----------------------|:---------------------------------------------------------------------|
| `MPRUN_SERVER_ADDRESS` | Address that the server should bind itself to                        |
| `MPRUN_DATA_PATH`      | Directory where the server should keep its database / blob directory |

### Worker

The woker can be run via

```bash
mprun_worker #(package entrypoint)
```

the worker is also configured via environment variables:

| Variable                | Effect                                |
|:------------------------|:--------------------------------------|
| `MPRUN_SERVER_ADDRESS`  | Address of the server                 |
| `MPRUN_WORKER_NAME`     | (Human-readable) Name for this worker |

### Client

The client can be run via

```bash
mprun_client #(package entrypoint)
```

for cli arguments, see `mprun_client --help`

## Job creation

The client can create a job from a TOML file.
The file format is as follows:

```toml
name = "testjob"

[params]
foo = [1, 2, 3]
bar = [true, false]
```

- `name` is the human-readable job name. This Name does not need to be unique, as each job will have its own unique ID.
- `params` can have an arbitrary number of parameters. Each parameter must be a list of arbitrary values.

## Development

While you are, of course, free to use whatever toolchain you want, we recommend [uv](https://docs.astral.sh/uv/).

After checking out the project, run 

```bash
uv sync --all-groups
```

to install the project & its dependencies.
While this step also creates a `.venv`, don't use it directly, but rather prefix commands with `uv run`.

Please install & setup [pre-commit](https://pre-commit.com/) with the provided hooks.
The CI-pipeline runs all the same checks, so check before committing.
The CI-pipeline *also* runs all the tests, so make sure those pass as well.
