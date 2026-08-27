#  Copyright (C) 2025 LEIDOS.
#
#  Licensed under the Apache License, Version 2.0 (the "License"); you may not
#  use this file except in compliance with the License. You may obtain a copy of
#  the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#  WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#  License for the specific language governing permissions and limitations under
#  the License.

import argparse
import math
import yaml
import subprocess
import shutil
import time
from pathlib import Path
from scenario_generator import ScenarioGenerator
from data_collector import DataCollector
from scenario_topology import apply_scenario_topology


CONFIG_DIRECTORY = Path(__file__).resolve().parent / "config"
MAP_DIRECTORY = CONFIG_DIRECTORY / "maps"
ROUTE_DIRECTORY = CONFIG_DIRECTORY / "routes"
MAP_TARGET = Path("/opt/carma/maps/vector_map.osm")
ROUTE_TARGET_DIRECTORY = Path("/opt/carma/routes")


class ScenarioRunner:
    """Run every scenario defined in parameters.yaml."""

    def __init__(
        self,
        parameters_path: str = "config/parameters/parameters.yaml",
        generate_only: bool = False,
    ):
        self.parameters_path = Path(parameters_path)
        self.generate_only = generate_only
        self.test_cases = []
        self.tmp_dir = Path("tmp").resolve()
        self.collector = DataCollector()

    def load_parameters(self):
        if not self.parameters_path.exists():
            raise FileNotFoundError(f"{self.parameters_path} not found")
        with open(self.parameters_path, 'r') as f:
            data = yaml.safe_load(f)
        self.test_cases = data.get("test_cases", [])
        if not self.test_cases:
            raise ValueError("No 'test_cases' found in parameters.yaml")
        print(f"Loaded {len(self.test_cases)} scenario(s)")

    @staticmethod
    def _configured_file(directory: Path, name: str, suffix: str) -> Path:
        """Resolve a configured map or route without leaving its config directory."""

        filename = name if name.endswith(suffix) else f"{name}{suffix}"
        source = (directory / filename).resolve()
        try:
            source.relative_to(directory.resolve())
        except ValueError as exc:
            raise ValueError(f"Invalid configured file name: {name}") from exc
        return source

    def _scenario_resources(self, case: dict, label: str) -> dict:
        """Validate and describe the map and vehicle routes used by a test case."""

        map_name = case.get("MAP")
        if not map_name:
            raise ValueError(
                f"MAP is not specified in parameters.yaml for test case {label}"
            )

        map_source = self._configured_file(MAP_DIRECTORY, str(map_name), ".osm")
        if not map_source.is_file():
            raise FileNotFoundError(
                f"{map_name} map specified in parameters.yaml for test case "
                f"{label} cannot be found at {map_source}"
            )

        routes = []
        route_targets = set()
        vehicles = case.get("env_settings", {}).get("vehicles", [])
        for vehicle in vehicles:
            settings = vehicle.get("settings", {})
            route_name = settings.get("SELECTED_ROUTE")
            if not route_name:
                continue

            vehicle_name = settings.get("VEHICLE_ID", vehicle.get("PROJECT_NAME"))
            route_source = self._configured_file(
                ROUTE_DIRECTORY, str(route_name), ".csv"
            )
            if not route_source.is_file():
                raise FileNotFoundError(
                    f"{route_name} route specified in parameters.yaml for "
                    f"vehicle {vehicle_name} cannot be found at {route_source}"
                )

            route_target = ROUTE_TARGET_DIRECTORY / route_source.name
            if route_target in route_targets:
                continue
            route_targets.add(route_target)
            routes.append(
                {
                    "source": str(route_source),
                    "target": str(route_target),
                    "name": str(route_name),
                    "vehicle": str(vehicle_name),
                }
            )

        return {
            "map_file": {
                "source": str(map_source),
                "target": str(MAP_TARGET),
                "name": str(map_name),
                "test_case": label,
            },
            "routes": routes,
        }

    @staticmethod
    def _normalize_vehicle_runtime_settings(case: dict) -> None:
        """Normalize vehicle values whose ROS 2 parameters require floats."""

        vehicles = case.get("env_settings", {}).get("vehicles", [])
        for vehicle in vehicles:
            settings = vehicle.get("settings", {})
            if "START_DELAY_IN_SECONDS" not in settings:
                continue

            value = settings["START_DELAY_IN_SECONDS"]
            vehicle_name = settings.get(
                "VEHICLE_ID", vehicle.get("PROJECT_NAME", "unknown")
            )
            if isinstance(value, bool):
                raise ValueError(
                    "START_DELAY_IN_SECONDS for vehicle "
                    f"{vehicle_name} must be numeric, not {value!r}"
                )
            try:
                normalized_value = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "START_DELAY_IN_SECONDS for vehicle "
                    f"{vehicle_name} must be numeric, not {value!r}"
                ) from exc
            if not math.isfinite(normalized_value):
                raise ValueError(
                    "START_DELAY_IN_SECONDS for vehicle "
                    f"{vehicle_name} must be finite, not {value!r}"
                )
            settings["START_DELAY_IN_SECONDS"] = normalized_value

    def _run_one(self, idx: int, case: dict):
        label = case.get("label", f"scenario_{idx}")
        scenario_resources = self._scenario_resources(case, label)
        case = apply_scenario_topology(case)
        self._normalize_vehicle_runtime_settings(case)
        print(f"\n=== Scenario {idx}: {label} ===")

        # 1. Clean tmp/ (fresh start)
        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir)
        self.tmp_dir.mkdir()

        # 2. Write parameter.yaml to tmp/
        param_path = self.tmp_dir / "parameter.yaml"
        with open(param_path, "w") as f:
            yaml.dump(
                {
                    "env_settings": case["env_settings"],
                    "scenario_resources": scenario_resources,
                },
                f,
                default_flow_style=False,
            )
        print(f"Generated {param_path}")

        # 3. Generate scripts
        gen = ScenarioGenerator(
            config_path=str(param_path),
            compose_root=self.parameters_path.parent,
        )
        scripts = gen.generate()
        start_sh = scripts["start_script"]
        stop_sh = scripts["stop_script"]

        if self.generate_only:
            print(
                f"Scenario {idx} generated. "
                f"Inspect files in {self.tmp_dir}.\n"
            )
            return

        try:
            # 4. Start
            print(f"Launching: {start_sh}")
            subprocess.run(["bash", start_sh], check=True)

            # 5. Wait
            runtime = case.get("runtime_seconds", 60)
            print(f"Running for {runtime} seconds...")
            time.sleep(runtime)
        except KeyboardInterrupt:
            print("Interrupt received; stopping the scenario...")
            raise
        finally:
            # 6. Stop
            print(f"Stopping: {stop_sh}")
            # Cleanup is best-effort so it does not hide the original startup
            # failure or keyboard interrupt.
            stop_result = subprocess.run(["bash", stop_sh], check=False)
            if stop_result.returncode != 0:
                print(
                    f"Warning: stop script exited with "
                    f"{stop_result.returncode}"
                )

        print("Collecting data outputs...")
        self.collector.collect(idx, case)

        # 7. ALWAYS clean tmp/
        shutil.rmtree(self.tmp_dir)
        print(f"Cleaned {self.tmp_dir}")

        print(f"Scenario {idx} complete.\n")

    def run(self):
        if not self.test_cases:
            self.load_parameters()

        for i, case in enumerate(self.test_cases, start=1):
            try:
                self._run_one(i, case)
            except KeyboardInterrupt:
                print(f"Scenario {i} interrupted by user")
                if not self.generate_only and self.tmp_dir.exists():
                    shutil.rmtree(self.tmp_dir)
                raise SystemExit(130)
            except Exception as e:
                print(f"Scenario {i} failed: {e}")
                if not self.generate_only and self.tmp_dir.exists():
                    shutil.rmtree(self.tmp_dir)


def parse_args():
    parser = argparse.ArgumentParser(description="Run CDASim scenarios")
    parser.add_argument(
        "--generate-only",
        action="store_true",
        help="Generate environment and start/stop files without executing them",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    ScenarioRunner(generate_only=args.generate_only).run()
