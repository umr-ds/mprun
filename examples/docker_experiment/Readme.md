# Docker experiment example

A minimal experiment that runs inside a Docker container.

The **`Dockerfile`** sets up the runtime environment (base image, dependencies, environment files).
The backend volume-mounts the experiment directory into the container at `/workspace` and runs the executable there.

## Files

| File               | Purpose                                                                       |
|:-------------------|:------------------------------------------------------------------------------|
| `experiment.toml`  | Experiment definition — uses the `DOCKER` backend.                            |
| `run.sh`           | Main executable — processes parameter arguments, writes output.               |
| `Dockerfile`       | Builds the container image — copies environment files, installs dependencies. |

## Submitting the experiment

```bash
mprun_client create experiment.toml
```

See the main [Readme.md](../../Readme.md) for the full field reference and backend documentation.
