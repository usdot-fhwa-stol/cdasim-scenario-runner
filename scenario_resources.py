#  Copyright (C) 2026 LEIDOS.
#
#  Licensed under the Apache License, Version 2.0 (the "License"); you may not
#  use this file except in compliance with the License. You may obtain a copy of
#  the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict


CONFIG_DIRECTORY = Path(__file__).resolve().parent / "config"
INFRASTRUCTURE_CONFIG_DIRECTORY = CONFIG_DIRECTORY / "infrastructure"
MAP_DIRECTORY = CONFIG_DIRECTORY / "maps"
ROUTE_DIRECTORY = CONFIG_DIRECTORY / "routes"
CDASIM_CONFIG_DIRECTORY = CONFIG_DIRECTORY / "cdasim"
MAP_TARGET_DIRECTORY = Path("/opt/carma/maps")
ROUTE_TARGET_DIRECTORY = Path("/opt/carma/routes")
V2XHUB_DOWNLOAD_DIRECTORY = Path("/opt/v2xhub/download")


class ScenarioResourceManager:
    """Resolve scenario resources and prepare resources needing transformation."""

    def __init__(self, tmp_dir: Path):
        self.tmp_dir = Path(tmp_dir).resolve()

    @staticmethod
    def _configured_file(directory: Path, name: str, suffix: str = "") -> Path:
        filename = name if name.endswith(suffix) else f"{name}{suffix}"
        source = (directory / filename).resolve()
        try:
            source.relative_to(directory.resolve())
        except ValueError as exc:
            raise ValueError(f"Invalid configured file name: {name}") from exc
        return source

    @staticmethod
    def _resource_env_name(name: str) -> str:
        normalized = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper()
        if not normalized:
            raise ValueError("Resource name cannot be empty")
        return f"{normalized}_RESOURCE_PATH"

    def _resolve_cdasim_resources(self, case: dict, label: str) -> list[dict]:
        configured = (
            case.get("env_settings", {})
            .get("cdasim", {})
            .get("settings", {})
            .get("CDASIM_RESOURCES", {})
        )
        resources = []
        for name, value in configured.items():
            files = [value] if isinstance(value, str) else value
            if not isinstance(files, list) or not files:
                raise ValueError(
                    f"CDASim resource {name!r} must specify a file name or "
                    "a non-empty list of file names"
                )
            for filename in files:
                if not isinstance(filename, str) or not filename:
                    raise ValueError(
                        f"CDASim resource {name!r} contains an invalid file "
                        f"name: {filename!r}"
                    )
                source = self._configured_file(
                    CDASIM_CONFIG_DIRECTORY / name, filename
                )
                if not source.is_file():
                    raise FileNotFoundError(
                        f"{name} configuration specified for test case "
                        f"{label} cannot be found in {source}!"
                    )
                resources.append(
                    {
                        "source": str(source),
                        "target": str(self.tmp_dir / name),
                        "name": str(name),
                        "test_case": str(label),
                    }
                )
        return resources

    def _resolve_routes(self, case: dict) -> list[dict]:
        routes = []
        targets = set()
        for vehicle in case.get("env_settings", {}).get("vehicles", []):
            settings = vehicle.get("settings", {})
            route_name = settings.get("SELECTED_ROUTE")
            if not route_name:
                continue
            vehicle_name = settings.get("VEHICLE_ID", vehicle.get("PROJECT_NAME"))
            source = self._configured_file(ROUTE_DIRECTORY, str(route_name), ".csv")
            if not source.is_file():
                raise FileNotFoundError(
                    f"{route_name} route specified for vehicle {vehicle_name} "
                    f"cannot be found at {source}"
                )
            target = ROUTE_TARGET_DIRECTORY / source.name
            if target not in targets:
                targets.add(target)
                routes.append(
                    {
                        "source": str(source),
                        "target": str(target),
                        "name": str(route_name),
                        "vehicle": str(vehicle_name),
                    }
                )
        return routes

    def _resolve_infrastructure_resources(self, case: dict) -> list[dict]:
        resources = []
        for infrastructure in (
            case.get("env_settings", {}).get("streets") or []
        ):
            name = infrastructure["PROJECT_NAME"]
            configured = infrastructure.get("settings", {}).get(
                "INFRASTRUCTURE_RESOURCES", {}
            )
            for resource_name, filename in configured.items():
                if not isinstance(filename, str) or not filename:
                    raise ValueError(
                        f"Infrastructure resource {resource_name!r} for "
                        f"{name} must specify one file name"
                    )
                source = self._configured_file(
                    INFRASTRUCTURE_CONFIG_DIRECTORY / resource_name, filename
                )
                if not source.is_file():
                    raise FileNotFoundError(
                        f"{resource_name} configuration specified for streets "
                        f"instance {name} cannot be found in {source}!"
                    )
                target = self.tmp_dir / f"{resource_name}_{name}"
                if resource_name == "sensor":
                    target = V2XHUB_DOWNLOAD_DIRECTORY
                resource = {
                    "source": str(source),
                    "target": str(target),
                    "name": str(resource_name),
                    "infrastructure": str(name),
                }
                if resource_name == "sensor":
                    sensor_settings = infrastructure.get("settings", {}).get(
                        "SENSORS"
                    ) or {}
                    sensor_id = sensor_settings.get("sensor_id")
                    sensor_type = sensor_settings.get("type")
                    if not sensor_id or not sensor_type:
                        raise ValueError(
                            "SENSORS.sensor_id and SENSORS.type are required "
                            f"for {name}"
                        )
                    resource["sensor_id"] = str(sensor_id)
                    resource["sensor_type"] = str(sensor_type)
                    sensor_location = sensor_settings.get("location")
                    if sensor_location is not None:
                        if (
                            "x" not in sensor_location
                            or "y" not in sensor_location
                        ):
                            raise ValueError(
                                "SENSORS.location.x and SENSORS.location.y "
                                f"are required for {name}"
                            )
                        resource["sensor_location"] = dict(sensor_location)
                resources.append(resource)
        return resources

    def resolve(self, case: dict, label: str) -> dict:
        """Validate and describe all files required by a test case."""

        map_name = case.get("MAP")
        if not map_name:
            raise ValueError(f"MAP is not specified for test case {label}")
        map_source = self._configured_file(MAP_DIRECTORY, str(map_name), ".osm")
        if not map_source.is_file():
            raise FileNotFoundError(
                f"{map_name} map specified for test case {label} cannot be "
                f"found at {map_source}"
            )
        return {
            "map_file": {
                "source": str(map_source),
                "target": str(MAP_TARGET_DIRECTORY / "vector_map.osm"),
                "name": str(map_name),
                "test_case": label,
            },
            "routes": self._resolve_routes(case),
            "infrastructure_configs": self._resolve_infrastructure_resources(case),
            "cdasim_configs": self._resolve_cdasim_resources(case, label),
        }

    def _prepare_sensor(
        self, resource: Dict[str, Any], infrastructure: Dict[str, Any]
    ) -> Path:
        source = Path(resource["source"])
        sensors = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(sensors, list) or len(sensors) != 1:
            raise ValueError(f"Expected exactly one sensor in {source}")
        sensor = sensors[0]
        sensor["sensorId"] = resource["sensor_id"]
        sensor["type"] = resource["sensor_type"]
        location = sensor.get("ref", {}).get("location")
        if not isinstance(location, dict):
            raise ValueError(
                f"Sensor {resource['sensor_id']!r} in {source} must define "
                "ref.location"
            )
        location.pop("_comment", None)
        if "sensor_location" in resource:
            location.update(resource["sensor_location"])

        generated_dir = (
            self.tmp_dir
            / f"generated-sensor-json-{resource['infrastructure']}"
        )
        generated_dir.mkdir(parents=True, exist_ok=True)
        generated = generated_dir / f"sensors-{resource['infrastructure']}.json"
        generated.write_text(json.dumps(sensors, indent=2) + "\n", encoding="utf-8")
        return generated

    def prepare_infrastructure(
        self, scenario_resources: dict, env_settings: dict
    ) -> None:
        """Transform configured resources and expose each staged directory."""

        infrastructures = {
            item["PROJECT_NAME"]: item
            for item in env_settings.get("streets", [])
        }
        processors: Dict[str, Callable[[dict, dict], Path]] = {
            "sensor": self._prepare_sensor,
        }
        for resource in scenario_resources.get("infrastructure_configs", []):
            infrastructure = infrastructures.get(resource["infrastructure"])
            if infrastructure is None:
                raise ValueError(
                    f"Infrastructure {resource['infrastructure']!r} was not configured"
                )
            processor = processors.get(resource["name"])
            if processor:
                resource["source"] = str(processor(resource, infrastructure))
            if resource["name"] == "sensor":
                infrastructure["settings"]["SENSOR_JSON_FILE_PATH"] = (
                    f"/var/www/download/{Path(resource['source']).name}"
                )
            else:
                infrastructure["settings"][
                    self._resource_env_name(resource["name"])
                ] = str(Path(resource["target"]).resolve())

    def cdasim_env_settings(self, cdasim: dict, scenario_resources: dict) -> dict:
        """Return CDASim settings containing absolute staged resource paths."""

        result = {**cdasim, "settings": dict(cdasim.get("settings", {}))}
        for resource in scenario_resources.get("cdasim_configs", []):
            result["settings"][self._resource_env_name(resource["name"])] = str(
                Path(resource["target"]).resolve()
            )
        return result
