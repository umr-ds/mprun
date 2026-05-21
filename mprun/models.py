"""Module contains Pydantic models."""

from __future__ import annotations

from enum import Enum, StrEnum, auto
from itertools import product
from os import X_OK, access
from pathlib import Path
from time import time
from uuid import uuid4, uuid5
from zipfile import ZIP_LZMA, ZipFile

from pydantic import BaseModel, ValidationInfo, field_validator
from tomlkit import dump, load

from mprun.errors import ArchiveValidationError, InvalidParametersError

type TOMLScalar = str | int | float | bool  # TOML-serialisable types for Job params

JOB_DEFINITION_NAME = "job_definition.toml"
JOB_ARCHIVE_NAME = "job_archive.zip"
RESULTS_ARCHIVE_NAME = "results.zip"


def _expand_parameters(
    params: dict[str, list[TOMLScalar]],
) -> list[dict[str, TOMLScalar]]:
    """Expand parameter set by creating cross-product of all parameters.

    Args:
        params (dict[str, list[TOMLScalar]]): Dictionary of Lists of parameters

    Returns:
        list[dict[str, TOMLScalar]]: List of Dicts of single parameter set, each containing one value from the provided lists.
                                     All possible permutations.
    """
    if not params:
        raise InvalidParametersError(reason="Empty parameters not allowed")

    for key, values in params.items():
        if not key:
            raise InvalidParametersError(
                reason="Parameter keys must not be empty string"
            )
        if not values:
            raise InvalidParametersError(reason="Parameter lists must not be empty")

    expanded: list[dict[str, TOMLScalar]] = []

    keys = list(params.keys())
    for values in product(*params.values()):
        expanded.append(dict(zip(keys, values, strict=True)))

    return expanded


class ActiveState(StrEnum):
    """Possible active states for both Jobs and Runs.

    Meaning for Run:
        WAITING: Run has not been dispatched.
        RUNNING: Run has been dispatched, has not finished.
        FINISHED: Run has finished.

    Meaning for Job:
        WAITING: All runs are waiting.
        RUNNING: At least one run is running.
        FINISHED: All runs have finished.
    """

    WAITING = "WAITING"
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"


class SuccessState(StrEnum):
    """Possible success states for Jobs and Runs.

    Meaning for Runs:
        PENDING: This Run has not finished
        SUCCESS: This Run has finished without error.
        FAILED: This Run has finished with an error.

    Meaning for Jobs:
        PENDING: There are still Runs with state PENDING, no Runs with state FAILED
        SUCCESS: All Runs have state SUCCESS
        FAILED: At least one Run has state FAILED
    """

    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class ValidationMode(Enum):
    """Different modes for validation of Jobs/JobDefinitions.

    DATA_ONLY: Only validate Job data (contents of Job/JobDefinition object).
    DATA_AND_FILES: Also validate Job files (check if filenames speicifed in Job actually exist).
    """

    DATA_ONLY = auto()
    DATA_AND_FILES = auto()


class JobDefinition(BaseModel):
    """Minimal info needed to create a new Job.

    Args:
        name (str): Human readable job name. Does not have to be unique.
        params (dict[str, list[TOMLScalar]]): Job's parameters. Each parameter should be a list of discrete TOML-serializable values (str, int, float, bool).
                                              Will be used to generate runs by computing cross product of parameter lists.
        executable (str): Name of the Job's main executable.
                          If loading from TOML, must point to a File located in the same directory as the TOML definition.
        results (dict[str, str]): Names of files/directories that should be saved after a Run.
                                  Keys are names in the worker's file system, values are the names that inside the results archive.
        setup_executable (str | None): Optional executable to be run before the main executable.
                                       If loading from TOML, must point to a File located in the same directory as the TOML definition.
        environment_variables (dict[str, str] | None): Optional dictionary of environment variables to set before running main executable.
        environment_files (dict[str, str] | None): Optional list of additional files/directories that should be copied to the worker's file system before running the main executable.
                                                   Each item should be of form {<file_name>: <copy_to>}.
    """

    name: str
    params: dict[str, list[TOMLScalar]]
    executable: str
    results: dict[str, str]
    setup_executable: str | None = None
    environment_variables: dict[str, str] | None = None
    environment_files: dict[str, str] | None = None

    def dump_toml(self, file_path: Path) -> None:
        """Dump contents of JobDefinition into ``file_path``.

        Args:
            file_path (Path): Path to TOML file.
        """
        data = self.model_dump(exclude_none=True)
        with file_path.open("w", encoding="utf-8") as f:
            dump(data=data, fp=f)

    @classmethod
    def load_toml(
        cls, file_path: Path, validation_mode: ValidationMode
    ) -> JobDefinition:
        """Load a JobDefinition from a TOML file.

        Returns:
            JobDefinition: Parsed & validated JobDefinition from file.
            validation_mode (ValidationMode): Extend of validation. (See ValidationMode for meanings)

        Raises:
            OSError: If reading file fails
            pydantic.ValidationError: If contents of file are not valid JobDefinition
        """
        with file_path.open("r") as f:
            if validation_mode == ValidationMode.DATA_ONLY:
                return cls.model_validate(load(f).unwrap(), strict=True)
            return cls.model_validate(
                load(f).unwrap(), strict=True, context=file_path.parent
            )

    @field_validator("executable", mode="after")
    @classmethod
    def validate_executable(cls, name: str, info: ValidationInfo) -> str:
        """Validate executable name.

        If a Path to a folder is provided, we check if there is a file inside the folder with the provided executable name,
        and whether that file is marked as executable.

        Args:
            name (str): Name of executable. Just the name, NOT the full path. Full path will be computed from ValidationInfo.
            info (ValidationInfo): If present, info.context must be Path pointing to directory where Job's files are located.

        Returns:
            str: Validates executable name

        Raises:
            ValueError: If validation fails.
        """
        if not isinstance(
            info.context, Path
        ):  # if no path is provided, there's no further evaluation to do.
            return name
        dir_path: Path = info.context
        if not dir_path.is_dir():  # the directory should actually be a directory...
            msg = f"{dir_path} not a directory"
            raise ValueError(msg)

        executable_path = dir_path / name
        if not executable_path.is_file(
            follow_symlinks=False
        ):  # a file with the name should exist
            msg = f"No such file: {executable_path}"
            raise ValueError(msg)
        if not access(executable_path, X_OK):  # the file should be marked executable
            msg = f"File {executable_path} not marked executable"
            raise ValueError(msg)

        return name

    @field_validator("setup_executable", mode="after")
    @classmethod
    def validate_setup(cls, name: str | None, info: ValidationInfo) -> str | None:
        """Validate optional setup executable.

        If a name is included, the validation logic is basically the same as with the main executable.

        Args:
            name (str | None): Name of executable. Just the name, NOT the full path. Full path will be computed from ValidationInfo.
                               If None, no validation happens.
            info (ValidationInfo): If present, info.context must be Path pointing to directory where Job's files are located.

        Returns:
            str | None: None, if no name was given, otherwise the vaidated name.

        Raises:
            ValueError: If validation fails.
        """
        if name is None:  # if no name is given, the nthere's nothing to do
            return None
        return cls.validate_executable(
            name=name, info=info
        )  # if a name is given, the validation logic is tha same as with the main executable.

    @field_validator("environment_files", mode="after")
    @classmethod
    def validate_env_files(
        cls, files: dict[str, str] | None, info: ValidationInfo
    ) -> dict[str, str] | None:
        """Validate optional list of environment files.

        Args:
            files (dict[str, str] | None): Dict of form {<file_name>: <copy_to>}. Method will check if `file_name` exists. Does not validate `copy_to`.
                                           If None, no validation happens.
            info (ValidationInfo): If present, info.context must be Path pointing to directory where Job's files are located.

        Returns:
            dict[str, str] | None: None, if no files were specified. Otherise, the validated list of files.

        Raises:
            ValueError: If validation fails.
        """
        if files is None:  # if no files were given, then there's nothing to do
            return None

        if not isinstance(
            info.context, Path
        ):  # if no path is provided, there's no further evaluation to do.
            return files
        dir_path: Path = info.context
        if not dir_path.is_dir():  # the directory should actually be a directory...
            msg = f"{dir_path} not a directory"
            raise ValueError(msg)

        for env_file in files:
            file_path = dir_path / env_file
            if not file_path.is_file(follow_symlinks=False):
                msg = f"No such file: {file_path}"
                raise ValueError(msg)

        return files

    def create_archive(self, job_toml: Path) -> Path:
        """Create archive for Job.

        Tries to create zip archive of all files specified in this JobDefinition.
        Created archive will be located in same directory as files.

        Args:
            job_toml (Path): Path to the Job's TOML file. All other Job files need to be located in the same directory.

        Returns:
            Path: Path of the created archive.

        Raises:
            FileNotFoundError: If ``job_toml``, its parent directory, or any referenced file
                (``executable``, ``setup_executable``, entries in ``environment_files``) does not exist.
            PermissionError: If the archive destination directory is not writable, or any source
                file is not readable.
            OSError: For other I/O failures (e.g. disk full) during archive creation or file writes.
            lzma.LZMAError: If LZMA compression fails (rare; typically memory pressure or corrupt data).
        """
        directory = job_toml.parent
        archive_path = directory / JOB_ARCHIVE_NAME
        with ZipFile(
            archive_path,
            mode="w",
            compression=ZIP_LZMA,
            allowZip64=True,
        ) as zf:
            zf.write(job_toml, JOB_DEFINITION_NAME)  # add JobDefinition itself
            zf.write(
                directory / self.executable, self.executable
            )  # add main executable
            if self.setup_executable:
                zf.write(
                    directory / self.setup_executable, self.setup_executable
                )  # add setup executable (if one is specified)
            if self.environment_files:
                for environment_file in self.environment_files:
                    zf.write(
                        directory / environment_file, environment_file
                    )  # add environment files (if any are specified)
        return archive_path

    def validate_archive(self, archive_path: Path) -> None:
        """Validate Job archive.

        Args:
            archive_path (Path): Path of the Job's archive

        Raises:
            ArchiveValidationError: If the archive's contents do not match the JobDefinition.
        """
        with ZipFile(archive_path, mode="r") as zf:
            contents = zf.namelist()
            if JOB_DEFINITION_NAME not in contents:
                msg = "Archive does not contain Job definition"
                raise ArchiveValidationError(reason=msg)
            with zf.open(JOB_DEFINITION_NAME, "r") as f:
                archive_definition = JobDefinition.model_validate(
                    load(f).unwrap(), strict=True
                )
                if archive_definition != self:
                    msg = "Job definition in archive is different"
                    raise ArchiveValidationError(reason=msg)

            if self.executable not in contents:
                msg = "Archive does not contain main executable"
                raise ArchiveValidationError(reason=msg)
            if self.setup_executable and self.setup_executable not in contents:
                msg = "Archive does not contain setup executable"
                raise ArchiveValidationError(reason=msg)
            if self.environment_files:
                for env_file in self.environment_files:
                    if env_file not in contents:
                        msg = f"Archive does not contain environment file {env_file}"
                        raise ArchiveValidationError(reason=msg)


class Job(BaseModel):
    """Parametrised job.

    The Job acts as the container for a list of Runs, which are generated during initialisation from the Job's parameters.

    Attributes:
        definition (JobDefinition): Job's metadata
        jid (int): Unique identifier of this job. Generated automatically from uuid.uuid4.
                   (Integer representation of a UUID for serialisability)
        name (str): Human readable job name. Does not have to be unique.
        active_state (ActiveState): Job's active state. See ActiveState enum for behaviour documentation.
        success_state (SuccessState): Job's success state. See SuccessState enum for behaviour documentation.
        runs (list[Run]): List of Runs that were generated from parameters.
    """

    definition: JobDefinition
    jid: int
    name: str
    active_state: ActiveState
    success_state: SuccessState
    runs: list[Run]

    def __str__(self) -> str:
        """Job's string representation."""
        return f"Job({self.name})"

    def __hash__(self) -> int:
        """Compute hash of Job."""
        return hash(self.jid)

    @property
    def active(self) -> bool:
        """Whether this Job is 'active'.

        A Job is active if its active_state is either WAITING or RUNNING.
        """
        return self.active_state in (ActiveState.WAITING, ActiveState.RUNNING)

    @property
    def waiting_runs(self) -> list[Run]:
        """Subset of Runs that have not been dispatched."""
        return [run for run in self.runs if run.active_state == ActiveState.WAITING]

    @classmethod
    def new(cls, definition: JobDefinition) -> Job:
        """Create new Job from a JobDefinition.

        Args:
            definition (JobDefinition): Definition of new Job.
        """
        jid = uuid4()
        active_state = ActiveState.WAITING
        success_state = SuccessState.PENDING
        runs: list[Run] = []
        expanded = _expand_parameters(definition.params)
        for index, param_set in enumerate(expanded):
            runs.append(
                Run(
                    definition=definition,
                    jid=jid.int,
                    rid=uuid5(namespace=jid, name=bytes(index)).int,
                    index=index,
                    name=f"{definition.name}-{index}",
                    active_state=active_state,
                    success_state=success_state,
                    params=param_set,
                ),
            )

        return Job(
            definition=definition,
            jid=jid.int,
            name=definition.name,
            active_state=active_state,
            success_state=success_state,
            runs=runs,
        )


class Run(BaseModel):
    """A single run of a Job.

    Attributes:
        jid (int): Job ID of the parent Job. (Integer representation of a UUID for serialisability)
        index (int): Run's index amongst its brethren.
        rid (int): Unique identifier for this Run. (Integer representation of a UUID for serialisability)
                    Generated with uuid.uuid5, using parent's job ID as namespace and index as name.
        wid (int | None): If this Run has been dispatched to a worker, this attribute contains the worker's ID.
                          None if Run has not yet been dispatched.
        name (str): Human-readable name. Generated using {job_name}-{index}.
        active_state (ActiveState): Run's active state. See active_state enum for behaviour documentation.
        success_state (SuccessState): Run's success state. See SuccessState enum for behaviour documentation.
        params (dict[str, TOMLScalar]): Run's parameter set. Has one value from each of the parent Job's parameter lists.
    """

    definition: JobDefinition
    jid: int
    index: int
    rid: int
    wid: int | None = None
    name: str
    active_state: ActiveState
    success_state: SuccessState
    params: dict[str, TOMLScalar]

    def __str__(self) -> str:
        """Return a string representation of the Job."""
        return f"Run({self.name})"

    def __hash__(self) -> int:
        """Compute hash of Run."""
        return hash(self.rid)

    def assemble_args(self) -> list[str]:
        """Assemble parameters into arguments to pass to executable."""
        args: list[str] = []

        for param, value in self.params.items():
            args.append(f"{param}={value}")

        return args


class WorkerState(StrEnum):
    """Possible states for Workers.

    IDLE: Worker is not currently executing a Job.
    WORKING: Worker is currently executing a Job.
    DEAD: Worker is unreachable.
    """

    IDLE = "IDLE"
    WORKING = "WORKING"
    DEAD = "DEAD"


class WorkerData(BaseModel):
    """A Worker.

    Attributes:
        wid (int): Unique identifier - integer representation of a UUID.
        name (str): Human readable name. Does not have to be unique, but is encouraged to be.
        state (WorkerState): Worker's state. See WorkerState enum for behaviour documentation.
        run: (int): If the worker is currently executing a Run, this attribute sotres that Run's ID.
    """

    wid: int
    name: str
    state: WorkerState
    last_check_in: float
    run: int | None = None

    @classmethod
    def new(cls, name: str) -> WorkerData:
        """Create new worker.

        Args:
            name (str): Human readable name. Does not have to be unique, but is encouraged to be.
        """
        return WorkerData(
            wid=uuid4().int, name=name, state=WorkerState.IDLE, last_check_in=time()
        )
