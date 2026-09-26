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

from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
import shutil


class DataCollector:

    def __init__(self):
        self.run_id = datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%S_%fZ"
        )

    def collect(self, index: int, config: dict):
        data_output = config.get("data_output")
        if not data_output:
            print("No data_output section. Skipping.")
            return None

        out_dir = Path(data_output["output_directory"])
        out_dir.mkdir(parents=True, exist_ok=True)

        label = config.get("label", f"scenario_{index}")
        case_dir = out_dir / self.run_id / data_output.get(
            "rename_format", "{label}"
        ).format(index=index, label=label)
        case_dir.mkdir(parents=True, exist_ok=True)

        collect_cfg = data_output.get("collect", {})

        for key, value in collect_cfg.items():
            print(key, value)
            self._collect_folder(Path(value), case_dir / key)

        return case_dir

    @staticmethod
    def _preserved_directory_tree(
        source: Path, protected_directories: Iterable[Path]
    ) -> set[Path]:
        """Return protected directories and their ancestors under source."""

        source = source.resolve(strict=False)
        preserved = {source}
        for directory in protected_directories:
            candidate = Path(directory).resolve(strict=False)
            try:
                candidate.relative_to(source)
            except ValueError:
                continue

            while True:
                preserved.add(candidate)
                if candidate == source:
                    break
                candidate = candidate.parent
        return preserved

    @classmethod
    def _clear_directory_contents(
        cls, directory: Path, preserved: set[Path]
    ) -> None:
        """Delete contents recursively while retaining preserved directories."""

        for child in directory.iterdir():
            if child.is_symlink() or not child.is_dir():
                child.unlink()
                continue

            cls._clear_directory_contents(child, preserved)
            if child.resolve(strict=False) not in preserved:
                child.rmdir()

    def clear_sources(
        self,
        config: dict,
        protected_directories: Iterable[Path] = (),
    ) -> None:
        """Clear collected files without removing required host directories.

        Files, symlinks, and other non-directory entries are removed
        recursively. Empty directories are removed unless they are a collection
        root, a protected directory, or an ancestor of a protected directory.
        """

        data_output = config.get("data_output")
        if not data_output:
            return

        for key, value in data_output.get("collect", {}).items():
            src = Path(value)
            if not src.exists():
                continue
            preserved = self._preserved_directory_tree(
                src, protected_directories
            )
            self._clear_directory_contents(src, preserved)
            print(f"Cleared {src}")

    def _collect_folder(self, src_base: Path, dest: Path):
        if src_base.exists():
            shutil.copytree(src_base, dest, dirs_exist_ok=True, symlinks=True)
            print(f"Copied {src_base} → {dest}")
        else:
            print(f"No logs found in: {src_base}")
