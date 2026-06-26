# Native experiment example

A minimal experiment that runs directly on the worker host without sandboxing.

## Files

| File               | Purpose                                                       |
|:-------------------|:--------------------------------------------------------------|
| `experiment.toml`  | Experiment definition — parameters, executables, results.     |
| `run.sh`           | Main executable — processes parameter arguments.              |

## Submitting the experiment

```bash
mprun_client create experiment.toml
```

See the main [Readme.md](../../Readme.md) for the full field reference and backend documentation.
