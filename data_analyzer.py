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

from pathlib import Path
import subprocess


class DataAnalyzer:

    def __init__(
        self,
        scripts_dir: str = "../cdasim-config/cdasim_data_analysis_scripts",
        image: str = "cdasim-data-analysis:latest"
    ):
        self.scripts_dir = Path(scripts_dir).resolve()
        self.image = image

    def analyze(self, case_dir: Path, case: dict):
        if not case_dir:
            print("No case_dir to analyze. Skipping.")
            return

        vehicle_names = []
        for vehicle in case.get("env_settings", {}).get("vehicles", []):
            vehicle_names.append(vehicle["settings"]["VEHICLE_ID"])

        mosaic_logs = case_dir / "mosaic_logs"
        log_dirs = [d for d in mosaic_logs.iterdir() if d.is_dir()] if mosaic_logs.exists() else []
        if not log_dirs:
            print(f"No log directories found in {mosaic_logs}. Skipping log analysis.")
        for log_dir in log_dirs:
            for vehicle_name in vehicle_names:
                self._run_log_analyzer(log_dir, vehicle_name, case_dir)

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
                    self._run_mcap_analyzer(mcap_file, vehicle_name)

            self._run_regression_analysis(rosbags, case_dir)
        else:
            print(f"No rosbags found in {case_dir}. Skipping mcap/control/regression analysis.")

    def _run_log_analyzer(self, log_dir: Path, vehicle_name: str, case_dir: Path):
        script = self.scripts_dir / "cdasim_log_analyzer.py"
        subprocess.run(
            ["python3", str(script), str(log_dir), "--vehicle-name", vehicle_name],
            check=True,
            cwd=case_dir,
        )

    def _run_mcap_analyzer(self, mcap_file: Path, vehicle_name: str):
        script = self.scripts_dir / "cdasim_mcap_analyzer.py"
        result = subprocess.run(
            ["python3", str(script), str(mcap_file), "--metric", "all", "--vehicle-name", vehicle_name]
        )
        if result.returncode != 0:
            print(f"Mcap analysis reported issues for {mcap_file} (exit code {result.returncode}).")

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
        result = subprocess.run(
            [
                "docker", "run", "--rm",
                "-v", f"{case_dir}:{case_dir}",
                self.image,
                "bash", "-c", command,
            ]
        )
        if result.returncode != 0:
            print(
                f"Control/regression analysis reported issues for {rosbags} "
                f"(exit code {result.returncode})."
            )
