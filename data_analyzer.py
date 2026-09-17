#  Copyright (C) 2026 LEIDOS.
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

from datetime import datetime
from pathlib import Path
import subprocess
import sys


class DataAnalyzer:

    def __init__(self, image: str = "cdasim-data-analysis:latest"):
        self.image = image

    def analyze(self, case_dir: Path, case: dict) -> list:
        """Runs every analyzer for one collected case, without aborting on a
        failure. Returns a list of failure descriptions (empty on full
        success) so callers can aggregate failures across cases."""
        if not case_dir:
            print("No case_dir to analyze. Skipping.")
            return ["no case_dir to analyze"]

        failures = []

        vehicle_names = []
        for vehicle in case.get("env_settings", {}).get("vehicles", []):
            vehicle_names.append(vehicle["settings"]["VEHICLE_ID"])

        mosaic_logs = case_dir / "mosaic_logs"
        log_dirs = [d for d in mosaic_logs.iterdir() if d.is_dir()] if mosaic_logs.exists() else []
        if not log_dirs:
            print(f"No log directories found in {mosaic_logs}. Skipping log analysis.")
        for log_dir in log_dirs:
            for vehicle_name in vehicle_names:
                success, log_path = self._run_log_analyzer(log_dir, vehicle_name, case_dir)
                if not success:
                    failures.append(
                        f"log analysis failed for {vehicle_name} in {log_dir} (see {log_path})"
                    )

        rosbags = case_dir / "rosbags"
        if rosbags.exists():
            for vehicle_name in vehicle_names:
                vehicle_dir = rosbags / vehicle_name
                if not vehicle_dir.exists():
                    print(f"No rosbags found for {vehicle_name} in {rosbags}. Skipping mcap analysis.")
                    continue
                mcap_files = list(vehicle_dir.rglob("*.mcap"))
                if not mcap_files:
                    print(f"No .mcap files found in {vehicle_dir}. Skipping mcap analysis.")
                for mcap_file in mcap_files:
                    success, log_path = self._run_mcap_analyzer(mcap_file, vehicle_name, case_dir)
                    if not success:
                        failures.append(
                            f"mcap analysis failed for {vehicle_name}: {mcap_file} (see {log_path})"
                        )

            success, log_path = self._run_regression_analysis(rosbags, case_dir)
            if not success:
                failures.append(f"control/regression analysis failed for {rosbags} (see {log_path})")
        else:
            print(f"No rosbags found in {case_dir}. Skipping mcap/control/regression analysis.")

        return failures

    def _run_in_container(
        self, docker_args: list, command: str, case_dir: Path, log_name: str
    ):
        """Runs `command` in the analysis image, streaming output live. On
        failure, also writes the captured command/stdout/stderr to a log
        file under case_dir so the detail survives past the terminal.
        Returns (success, log_path) - log_path is None on success."""
        result = subprocess.run(
            ["docker", "run", "--rm", *docker_args, self.image, "bash", "-c", command],
            capture_output=True,
            text=True,
        )
        print(result.stdout, end="")
        print(result.stderr, end="", file=sys.stderr)

        if result.returncode == 0:
            return True, None

        log_dir = case_dir / "docker_analysis_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = log_dir / f"{log_name}_{timestamp}.log"
        log_path.write_text(
            f"command: {command}\n"
            f"exit code: {result.returncode}\n\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}\n"
        )
        print(f"Failure details written to {log_path}")
        return False, log_path

    def _run_log_analyzer(self, log_dir: Path, vehicle_name: str, case_dir: Path):
        command = (
            "source /opt/ros/humble/setup.bash && "
            f"python3 /home/carma/cdasim_data_analysis_scripts/cdasim_log_analyzer.py "
            f"{log_dir} --vehicle-name {vehicle_name}"
        )
        success, log_path = self._run_in_container(
            ["-v", f"{case_dir}:{case_dir}", "-w", str(case_dir)],
            command,
            case_dir,
            f"log_analyzer_{vehicle_name}_{log_dir.name}",
        )
        if not success:
            print(f"Log analysis failed for {vehicle_name} in {log_dir}.")
        return success, log_path

    def _run_mcap_analyzer(self, mcap_file: Path, vehicle_name: str, case_dir: Path):
        command = (
            "source /opt/ros/humble/setup.bash && "
            f"python3 /home/carma/cdasim_data_analysis_scripts/cdasim_mcap_analyzer.py "
            f"{mcap_file} --metric all --vehicle-name {vehicle_name}"
        )
        success, log_path = self._run_in_container(
            ["-v", f"{case_dir}:{case_dir}"],
            command,
            case_dir,
            f"mcap_analyzer_{vehicle_name}_{mcap_file.stem}",
        )
        if not success:
            print(f"Mcap analysis reported issues for {mcap_file}.")
        return success, log_path

    def _run_regression_analysis(self, rosbags: Path, case_dir: Path):
        setup = (
            "source /opt/ros/humble/setup.bash && "
            "source /home/carma/msgs_ws/install/setup.bash"
        )
        control_cmd = (
            f"python3 /home/carma/carma-platform-scripts/run_all_control_analysis.py "
            f"--input-dir {rosbags} --output-dir {case_dir}"
        )
        regression_cmd = (
            f"python3 /home/carma/carma-platform-scripts/run_all_regression_analysis.py "
            f"--input-dir {rosbags} --output-dir {case_dir}"
        )
        command = f"{setup} && {control_cmd}; {regression_cmd}"
        success, log_path = self._run_in_container(
            ["-v", f"{case_dir}:{case_dir}"],
            command,
            case_dir,
            "regression_analysis",
        )
        if not success:
            print(f"Control/regression analysis reported issues for {rosbags}.")
        return success, log_path
