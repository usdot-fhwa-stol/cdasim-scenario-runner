#  Copyright (C) 2026 LEIDOS.
#
#  Licensed under the Apache License, Version 2.0 (the "License"); you may not
#  use this file except in compliance with the License. You may obtain a copy of
#  the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0

import stat
import subprocess
from pathlib import Path

from scenario_resources import MAP_TARGET_DIRECTORY, ROUTE_TARGET_DIRECTORY


SIMULATION_TMP_DIRECTORY = Path("/opt/carma-simulation/tmp")
SIMULATION_LOG_DIRECTORY = Path("/opt/carma-simulation/logs")
CARLA_SENSOR_LIB_LOG_DIRECTORY = Path("/opt/carla-sensor-lib")
CARMA_LOG_DIRECTORY = Path("/opt/carma/logs")
V2XHUB_DIRECTORY = Path("/opt/v2xhub")
LEGACY_V2XHUB_LOG_DIRECTORY = Path("/tmp/cdasim-scenario-runner")
DEFAULT_HOST_DIRECTORIES = {
    SIMULATION_TMP_DIRECTORY,
    SIMULATION_LOG_DIRECTORY,
    CARLA_SENSOR_LIB_LOG_DIRECTORY,
    CARMA_LOG_DIRECTORY,
    MAP_TARGET_DIRECTORY,
    ROUTE_TARGET_DIRECTORY,
    V2XHUB_DIRECTORY / "download",
    V2XHUB_DIRECTORY / "ssl",
    V2XHUB_DIRECTORY / "logs",
}
ALLOWED_HOST_DIRECTORY_ROOTS = DEFAULT_HOST_DIRECTORIES | {
    V2XHUB_DIRECTORY,
    LEGACY_V2XHUB_LOG_DIRECTORY,
}


def _validated_directory(path: Path) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise ValueError(f"Host directory must be absolute: {candidate}")
    resolved = candidate.resolve(strict=False)
    allowed = [path.resolve(strict=False) for path in ALLOWED_HOST_DIRECTORY_ROOTS]
    if not any(resolved == root or root in resolved.parents for root in allowed):
        raise ValueError(f"Host directory is outside allowed roots: {resolved}")
    return resolved


def host_directories(case: dict) -> list[Path]:
    """Return validated host directories required by a normalized test case."""

    directories = set(DEFAULT_HOST_DIRECTORIES)
    collect = case.get("data_output", {}).get("collect", {})
    directories.update(Path(path) for path in collect.values() if path)

    rosbag_root = collect.get("rosbags")
    if rosbag_root:
        for vehicle in case.get("env_settings", {}).get("vehicles", []):
            vehicle_id = str(vehicle["settings"]["VEHICLE_ID"])
            vehicle_part = Path(vehicle_id)
            if (
                vehicle_part.is_absolute()
                or len(vehicle_part.parts) != 1
                or vehicle_id in {".", ".."}
            ):
                raise ValueError(
                    f"VEHICLE_ID must be one path component: {vehicle_id!r}"
                )
            directories.add(Path(rosbag_root) / vehicle_id)
    return sorted({_validated_directory(path) for path in directories}, key=str)


def _prepare_directory(directory: Path) -> None:
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        print(f"Requesting elevated permission to create {directory}...")
        subprocess.run(["sudo", "mkdir", "-p", "--", str(directory)], check=True)

    mode = stat.S_IMODE(directory.stat().st_mode)
    if mode & 0o777 == 0o777:
        return
    try:
        directory.chmod(mode | 0o777)
    except PermissionError:
        print(f"Requesting elevated permission to make {directory} writable...")
        subprocess.run(
            ["sudo", "chmod", "a+rwx", "--", str(directory)], check=True
        )


def prepare_host_directories(case: dict) -> list[Path]:
    """Create required host directories and return their validated paths."""

    directories = host_directories(case)
    for directory in directories:
        _prepare_directory(directory)
        print(f"Prepared {directory}")
    return directories
