"""Tests for models module."""

import json
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_LZMA, ZipFile

import pytest

from mprun.custom_types import ActiveState, SuccessState, WorkerBackend
from mprun.errors import ArchiveValidationError
from mprun.models import (
    EXPERIMENT_ARCHIVE_NAME,
    EXPERIMENT_DEFINITION_NAME,
    EXPERIMENT_MANIFEST_NAME,
    Experiment,
    ExperimentDefinition,
    Run,
    ValidationMode,
)
from tests.conftest import (
    TEST_DOCKER_EXPERIMENT,
    TEST_DOCKER_EXPERIMENT_FILE,
    TEST_NATIVE_EXPERIMENT,
    TEST_NATIVE_EXPERIMENT_FILE,
)


def test_experiment_creation() -> None:
    """Static regression anchor: 3 * 3 * 2 parameters expand to 18 runs."""
    definition = ExperimentDefinition(
        name="exp",
        params={"foo": [1, 2, 3], "bar": ["a", "b", "c"], "buzz": [True, False]},
        executable="exe",
        backends={WorkerBackend.NATIVE},
        results={},
    )
    experiment = Experiment.new(definition)
    assert len(experiment.runs) == 18


def test_bundled_definition_matches_constant() -> None:
    """The bundled experiment_definition.toml stays in sync with TEST_NATIVE_EXPERIMENT.

    Guards against drift between the real artefact under
    ``tests/artefacts/test_native_experiment/`` and the in-memory constant the
    smoke test compares against.
    """
    loaded = ExperimentDefinition.load_toml(
        TEST_NATIVE_EXPERIMENT_FILE, validation_mode=ValidationMode.DATA_AND_FILES
    )
    assert loaded == TEST_NATIVE_EXPERIMENT


def test_experiment_archive(
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """``create_archive`` writes a zip in the experiment directory that validates."""
    definition, directory = make_experiment(
        setup=True, environment_files={"env.txt": "/destination/env"}
    )
    archive_path = definition.create_archive(
        experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
    )

    assert archive_path == directory / EXPERIMENT_ARCHIVE_NAME
    assert archive_path.is_file()
    definition.validate_archive(archive_path=archive_path)


def _tamper_archive_file(
    archive_path: Path, target_name: str, new_content: bytes
) -> Path:
    """Copy ``archive_path``, replacing ``target_name`` with ``new_content``.

    All other entries are copied byte-for-byte, so the manifest (and its
    stored hashes) remain identical to the original.
    """
    buf = BytesIO()
    with (
        ZipFile(archive_path, "r") as zin,
        ZipFile(buf, mode="w", compression=ZIP_LZMA, allowZip64=True) as zout,
    ):
        for item in zin.infolist():
            if item.filename == target_name:
                zout.writestr(item, new_content)
            else:
                zout.writestr(item, zin.read(item.filename))
    tampered = archive_path.with_name("tampered_" + archive_path.name)
    tampered.write_bytes(buf.getvalue())
    return tampered


def test_archive_hash_mismatch_executable(
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """Tampered main executable triggers a hash mismatch."""
    definition, directory = make_experiment(
        setup=True, environment_files={"env.txt": "/dest/env"}
    )
    archive_path = definition.create_archive(
        experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
    )
    tampered = _tamper_archive_file(archive_path, definition.executable, b"tampered\n")
    with pytest.raises(ArchiveValidationError) as exc_info:
        definition.validate_archive(archive_path=tampered)
    assert "hash mismatch" in exc_info.value.reason


def test_archive_hash_mismatch_setup(
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """Tampered setup executable triggers a hash mismatch."""
    definition, directory = make_experiment(
        setup=True, environment_files={"env.txt": "/dest/env"}
    )
    archive_path = definition.create_archive(
        experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
    )
    tampered = _tamper_archive_file(archive_path, "setup.py", b"tampered\n")
    with pytest.raises(ArchiveValidationError) as exc_info:
        definition.validate_archive(archive_path=tampered)
    assert "hash mismatch" in exc_info.value.reason


def test_archive_hash_mismatch_definition(
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """Tampered experiment definition triggers a hash mismatch."""
    definition, directory = make_experiment(
        setup=True, environment_files={"env.txt": "/dest/env"}
    )
    archive_path = definition.create_archive(
        experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
    )
    tampered = _tamper_archive_file(
        archive_path, EXPERIMENT_DEFINITION_NAME, b"tampered\n"
    )
    with pytest.raises(ArchiveValidationError) as exc_info:
        definition.validate_archive(archive_path=tampered)
    assert "hash mismatch" in exc_info.value.reason


def test_archive_hash_mismatch_env_file(
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """Tampered environment file triggers a hash mismatch."""
    definition, directory = make_experiment(
        setup=True, environment_files={"env.txt": "/dest/env"}
    )
    archive_path = definition.create_archive(
        experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
    )
    tampered = _tamper_archive_file(archive_path, "env.txt", b"tampered\n")
    with pytest.raises(ArchiveValidationError) as exc_info:
        definition.validate_archive(archive_path=tampered)
    assert "hash mismatch" in exc_info.value.reason


def test_archive_missing_hash_key(
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """Manifest missing a hash for an environment file raises KeyError."""
    definition, directory = make_experiment(
        setup=True, environment_files={"env.txt": "/dest/env"}
    )
    archive_path = definition.create_archive(
        experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
    )
    buf = BytesIO()
    with (
        ZipFile(archive_path, "r") as zin,
        ZipFile(buf, mode="w", compression=ZIP_LZMA, allowZip64=True) as zout,
    ):
        for item in zin.infolist():
            if item.filename == EXPERIMENT_MANIFEST_NAME:
                hashes = json.loads(zin.read(item.filename))
                hashes.pop("env.txt")
                zout.writestr(item, json.dumps(hashes))
            else:
                zout.writestr(item, zin.read(item.filename))
    corrupted = archive_path.with_name("corrupted_" + archive_path.name)
    corrupted.write_bytes(buf.getvalue())
    with pytest.raises(KeyError):
        definition.validate_archive(archive_path=corrupted)


def test_run_reset() -> None:
    """Run.reset() clears wid, timestamps, failure_reason and reverts to WAITING/PENDING."""
    definition = ExperimentDefinition(
        name="test",
        params={"x": [1]},
        executable="main.py",
        backends={WorkerBackend.NATIVE},
        results={},
    )
    run = Run(
        definition=definition,
        eid=1,
        index=0,
        iteration=0,
        wid=5,
        active_state=ActiveState.FINISHED,
        success_state=SuccessState.FAILED,
        failure_reason="something",
        started_running=123.0,
        finished_running=456.0,
        params={"x": 1},
    )
    run.reset()
    assert run.wid is None
    assert run.active_state == ActiveState.WAITING
    assert run.success_state == SuccessState.PENDING
    assert run.failure_reason is None
    assert run.started_running is None
    assert run.finished_running is None


DOCKERFILE_STUB = """\
FROM python:3.12-slim
WORKDIR /workspace
"""


def test_docker_archive_contains_dockerfile(
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """Archive built with DOCKER backend includes the Dockerfile."""
    definition, directory = make_experiment(backends={WorkerBackend.DOCKER})
    (directory / "Dockerfile").write_text(DOCKERFILE_STUB)

    archive_path = definition.create_archive(
        experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
    )
    assert archive_path.is_file()

    with ZipFile(archive_path, "r") as zf:
        assert "Dockerfile" in zf.namelist()
        assert zf.read("Dockerfile") == DOCKERFILE_STUB.encode()

    # Must pass validation with Dockerfile present
    definition.validate_archive(archive_path=archive_path)


def test_docker_archive_hash_mismatch_dockerfile(
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """Tampered Dockerfile triggers ArchiveValidationError."""
    definition, directory = make_experiment(backends={WorkerBackend.DOCKER})
    (directory / "Dockerfile").write_text(DOCKERFILE_STUB)

    archive_path = definition.create_archive(
        experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
    )
    tampered = _tamper_archive_file(archive_path, "Dockerfile", b"tampered\n")

    with pytest.raises(ArchiveValidationError) as exc_info:
        definition.validate_archive(archive_path=tampered)
    assert "hash mismatch" in exc_info.value.reason


def test_docker_archive_missing_dockerfile(
    tmp_path: Path,
) -> None:
    """Archive built without Dockerfile fails validation when DOCKER in backends."""
    directory = tmp_path / "experiment"
    directory.mkdir()

    main_py = directory / "main.py"
    main_py.write_text("#!/usr/bin/env python3\npass\n")
    main_py.chmod(0o755)

    definition = ExperimentDefinition(
        name="exp",
        params={"x": [1]},
        backends={WorkerBackend.DOCKER},
        executable="main.py",
        results={},
    )
    definition.dump_toml(directory / EXPERIMENT_DEFINITION_NAME)

    # create_archive skips missing files silently — the archive still passes
    # creation. But validation should fail because Dockerfile is missing.
    archive_path = definition.create_archive(
        experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
    )

    # Validation fails — no Dockerfile in archive but DOCKER in backends
    with pytest.raises(ArchiveValidationError) as exc_info:
        definition.validate_archive(archive_path=archive_path)
    assert "Dockerfile" in exc_info.value.reason


def test_bundled_docker_definition_matches_constant() -> None:
    """The bundled docker experiment_definition.toml stays in sync with TEST_DOCKER_EXPERIMENT."""
    loaded = ExperimentDefinition.load_toml(
        TEST_DOCKER_EXPERIMENT_FILE,
        validation_mode=ValidationMode.DATA_AND_FILES,
    )
    assert loaded == TEST_DOCKER_EXPERIMENT
