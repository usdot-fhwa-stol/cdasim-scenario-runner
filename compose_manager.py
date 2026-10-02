#  Copyright (C) 2026 LEIDOS.
#
#  Licensed under the Apache License, Version 2.0 (the "License"); you may not
#  use this file except in compliance with the License. You may obtain a copy of
#  the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


CONFIG_DIRECTORY = Path(__file__).resolve().parent / "config"
DEFAULT_CONFIG_OVERRIDE_DIRECTORY = CONFIG_DIRECTORY / "vehiclecfg"


class ComposeManager:
    """Resolve Compose inputs and stage config-image overrides."""

    CONFIG_INIT_COMMAND = "cp -a /root/vehicle/config/. /opt/carma/vehicle/config/"
    LEGACY_SERVICE_ALIASES = {
        "carma-simulation": "cdasim",
        "platform": "platform_ros1",
        "msger_roscore": "messenger_roscore",
        "msger_ros1_bridge": "messenger_ros1_bridge",
        "v2x_ros_driver": "v2x-ros-driver",
        "messenger_v2x_ros_driver": "messenger-v2x-ros-driver",
    }

    def __init__(self, tmp_dir: Path, compose_root: Path):
        self.tmp_dir = Path(tmp_dir).resolve()
        self.compose_root = Path(compose_root).resolve()
        self.config_containers: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def _service_name(cls, name: str) -> str:
        base_name = re.sub(r"_\d+$", "", name)
        return cls.LEGACY_SERVICE_ALIASES.get(base_name, base_name)

    @classmethod
    def _config_init_command(cls, compose_path: Optional[str]) -> str:
        if not compose_path:
            return cls.CONFIG_INIT_COMMAND
        config_dir = Path(compose_path).parent
        if config_dir == Path("/opt/carma/vehicle/config"):
            return cls.CONFIG_INIT_COMMAND
        try:
            relative = config_dir.relative_to("/opt")
        except ValueError:
            return cls.CONFIG_INIT_COMMAND
        return f"cp -a {Path('/root') / relative}/. {config_dir}/"

    @staticmethod
    def _service_reference(value: str, renames: Dict[str, str]) -> str:
        if value in renames:
            return renames[value]
        parts = value.split(":")
        if len(parts) >= 2 and parts[0] in ("service", "container"):
            parts[1] = renames.get(parts[1], parts[1])
            return ":".join(parts)
        return value

    @classmethod
    def normalize_services(cls, compose_path: Path) -> None:
        compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        services = compose.get("services", {})
        renames = {name: cls._service_name(name) for name in services}
        if all(name == renamed for name, renamed in renames.items()):
            return
        if len(set(renames.values())) != len(renames):
            raise ValueError("Legacy Compose service names normalize to duplicates")

        compose["services"] = {
            renames[name]: service for name, service in services.items()
        }
        for service in compose["services"].values():
            depends_on = service.get("depends_on")
            if isinstance(depends_on, list):
                service["depends_on"] = [
                    renames.get(name, name) for name in depends_on
                ]
            elif isinstance(depends_on, dict):
                service["depends_on"] = {
                    renames.get(name, name): settings
                    for name, settings in depends_on.items()
                }
            for key in ("network_mode",):
                value = service.get(key)
                if isinstance(value, str):
                    service[key] = cls._service_reference(value, renames)
            volumes_from = service.get("volumes_from")
            if isinstance(volumes_from, list):
                service["volumes_from"] = [
                    cls._service_reference(value, renames)
                    for value in volumes_from
                ]

        compose_path.write_text(
            yaml.safe_dump(compose, sort_keys=False), encoding="utf-8"
        )

    def extract_from_image(
        self,
        full_image: str,
        project_name: str,
        compose_path: Optional[str] = None,
        pull_policy: str = "missing",
    ) -> str:
        if not full_image:
            raise ValueError(f"Missing CONFIG_IMAGE_FULL for {project_name}")

        print(f"Checking config image: {full_image}")
        inspect = subprocess.run(
            ["docker", "image", "inspect", full_image],
            capture_output=True,
            text=True,
        )
        if pull_policy == "always" or (
            pull_policy == "missing" and inspect.returncode != 0
        ):
            subprocess.run(["docker", "pull", full_image], check=True)
            print(f"Pulled: {full_image}")
        elif inspect.returncode != 0:
            raise RuntimeError(f"Image not found: {full_image}")
        else:
            print(f"Using local image: {full_image}")

        container_name = f"inspect-{project_name}-{os.urandom(4).hex()}"
        init_command = self._config_init_command(compose_path)
        print(f"Starting inspection container: {container_name}")
        try:
            if compose_path:
                subprocess.run(
                    [
                        "docker", "run", "--name", container_name,
                        "--entrypoint", "sh", full_image, "-c", init_command,
                    ],
                    check=True,
                )
                source = compose_path
            else:
                subprocess.run(
                    [
                        "docker", "run", "-d", "--name", container_name,
                        full_image, "sleep", "infinity",
                    ],
                    check=True,
                )
                result = subprocess.run(
                    [
                        "docker", "exec", container_name, "find", "/",
                        "-type", "f", "-name", "docker-compose.yml",
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                files = [line for line in result.stdout.splitlines() if line]
                if not files:
                    raise FileNotFoundError(f"No docker-compose.yml in {full_image}")
                if len(files) > 1:
                    raise RuntimeError(
                        f"Multiple docker-compose.yml in {full_image}: {files}"
                    )
                source = files[0]

            if compose_path:
                destination_dir = self.tmp_dir / f"config-{project_name}"
                destination_dir.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    [
                        "docker", "cp", f"{container_name}:{Path(source).parent}/.",
                        str(destination_dir),
                    ],
                    check=True,
                )
                destination = destination_dir / Path(source).name
            else:
                destination = self.tmp_dir / f"docker-compose-{project_name}.yml"
                subprocess.run(
                    ["docker", "cp", f"{container_name}:{source}", str(destination)],
                    check=True,
                )

            self.normalize_services(destination)
            self.config_containers[project_name] = {
                "name": f"{project_name}-config",
                "image": full_image,
                "init_command": init_command,
                "overrides": [],
            }
            print(f"Extracted {source} → {destination}")
            return str(destination)
        finally:
            subprocess.run(
                ["docker", "rm", "-fv", container_name], capture_output=True
            )

    def resolve_path(self, configured_path: str) -> str:
        path = Path(configured_path)
        if not path.is_absolute():
            path = self.compose_root / path
        return str(path.resolve())

    def _base_compose(self, component: dict, project_name: str) -> str:
        configured = component.get("COMPOSE_FILE")
        if configured:
            if configured.startswith("oci://"):
                return configured
            return self.resolve_path(configured)
        return self.extract_from_image(
            component.get("CONFIG_IMAGE_FULL"),
            project_name,
            component.get("CONFIG_COMPOSE_PATH"),
            component.get("CONFIG_IMAGE_PULL_POLICY", "missing"),
        )

    def compose_files(self, component: dict, project_name: str) -> list[str]:
        files = [self._base_compose(component, project_name)]
        self._stage_config_overrides(component, project_name)
        for key in ("COMPOSE_OVERRIDES", "INTERNAL_COMPOSE_OVERRIDES"):
            files.extend(self.resolve_path(path) for path in component.get(key, []))
        return files

    def _stage_config_overrides(self, component: dict, project_name: str) -> None:
        overrides = component.get("CONFIG_OVERRIDES", {})
        if not overrides:
            return
        if not isinstance(overrides, dict):
            raise ValueError(f"CONFIG_OVERRIDES for {project_name} must be a mapping")

        compose_path = component.get("CONFIG_COMPOSE_PATH")
        config_container = self.config_containers.get(project_name)
        if not compose_path or not config_container:
            raise ValueError(
                f"CONFIG_OVERRIDES for {project_name} requires a config image "
                "and CONFIG_COMPOSE_PATH"
            )

        source_root = CONFIG_DIRECTORY.resolve()
        local_root = (self.tmp_dir / f"config-{project_name}").resolve()
        container_root = Path(compose_path).parent
        for target_name, source_name in overrides.items():
            if not all(
                isinstance(value, str) and value
                for value in (target_name, source_name)
            ):
                raise ValueError(f"Invalid CONFIG_OVERRIDES entry for {project_name}")
            source_path = Path(source_name)
            source_dir = (
                DEFAULT_CONFIG_OVERRIDE_DIRECTORY
                if len(source_path.parts) == 1
                else source_root
            )
            source = (source_dir / source_path).resolve()
            target = (local_root / target_name).resolve()
            try:
                source.relative_to(source_root)
                relative_target = target.relative_to(local_root)
            except ValueError as exc:
                raise ValueError(
                    f"CONFIG_OVERRIDES paths for {project_name} must stay "
                    "inside their config directories"
                ) from exc
            if not source.is_file() or not target.is_file():
                raise FileNotFoundError(
                    f"Invalid CONFIG_OVERRIDES file for {project_name}: "
                    f"{source} → {target}"
                )
            shutil.copy2(source, target)
            config_container["overrides"].append(
                {
                    "source": str(target),
                    "target": str(container_root / relative_target),
                }
            )
            print(f"Staged {source} → {target} for {project_name}")
