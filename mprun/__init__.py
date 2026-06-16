"""Multi-Parameter Experiment Runner."""

import pathlib

import platformdirs

from mprun.custom_types import AppPaths

PACKAGE_NAME = "mprun"
__version__ = "0.1.0"
__all__ = [
    "DEFAULT_CONFIG_DIRS",
    "DEFAULT_DATA_DIRS",
    "PACKAGE_NAME",
    "SERVER_ADDRESS_ENV",
    "AppPaths",
    "__version__",
    "client",
    "custom_types",
    "errors",
    "experiment_manager",
    "log",
    "models",
    "server",
    "worker",
]

SERVER_ADDRESS_ENV = "MPRUN_SERVER_ADDRESS"

# default directories
DEFAULT_DATA_DIRS = AppPaths(
    user=pathlib.Path(platformdirs.user_data_dir(appname=PACKAGE_NAME)),
    site=pathlib.Path(platformdirs.site_data_dir(appname=PACKAGE_NAME)),
)
DEFAULT_CONFIG_DIRS = AppPaths(
    user=pathlib.Path(platformdirs.user_config_dir(appname=PACKAGE_NAME)),
    site=pathlib.Path(platformdirs.site_config_dir(appname=PACKAGE_NAME)),
)
