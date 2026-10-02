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

import json
import re
import shlex
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from jinja2 import Template
from compose_manager import ComposeManager
from scenario_resources import ScenarioResourceManager


CDASIM_RUNTIME_TEMPLATE_PATH = (
    Path(__file__).resolve().parent
    / "config"
    / "cdasim"
    / "runtime.template.json"
)
STREET_NETWORK_OVERRIDE_PATH = (
    Path(__file__).resolve().parent
    / "config"
    / "compose"
    / "street-network.override.yml"
)


class ScenarioGenerator:
    """
    Generate sim_start.sh + sim_stop.sh from parameter.yaml.
    All files go into ./tmp/ (full paths).
    """

    V2X_PARAMS_TARGET = (
        "/opt/carma/install/v2x_ros_driver/share/"
        "v2x_ros_driver/config/params.yaml"
    )

    def __init__(
        self,
        config_path='tmp/scenario.yaml',
        start_template='config/templates/sim_start_template.sh.j2',
        stop_template='config/templates/sim_stop_template.sh.j2',
        tmp_dir='tmp',
        compose_root=None
    ):
        self.config_path = Path(config_path)
        self.start_template = Path(start_template)
        self.stop_template = Path(stop_template)
        self.config: Dict[str, Any] = {}
        self.tmp_dir: Path = Path(tmp_dir).resolve()
        compose_root = Path(
            compose_root if compose_root is not None else self.config_path.parent
        ).resolve()
        self.data: Dict[str, Any] = {}  # will hold scenario + temp_dir
        self.compose = ComposeManager(self.tmp_dir, compose_root)
        self.resource_manager = ScenarioResourceManager(self.tmp_dir)

    # --------------------------------------------------------------------- #
    # 1. Load config + create ./tmp/
    # --------------------------------------------------------------------- #
    def load_config(self) -> None:
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config not found: {self.config_path}")
        with open(self.config_path, 'r') as f:
            self.config = yaml.safe_load(f)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        print(f"Using tmp directory: {self.tmp_dir}")

    # --------------------------------------------------------------------- #
    # 2. Generate .env file (flattened)
    # --------------------------------------------------------------------- #
    def generate_env_file(self, env_filename: str, env_settings: Dict) -> str:
        project_name = env_settings.get('PROJECT_NAME')
        runtime_org = env_settings.get('RUNTIME_IMAGE_ORG')
        runtime_tag = env_settings.get('RUNTIME_IMAGE_TAG')
        settings = env_settings.get('settings', {})

        base = {}
        if project_name: base['PROJECT_NAME'] = project_name
        if runtime_org:  base['RUNTIME_IMAGE_ORG'] = runtime_org
        if runtime_tag:  base['RUNTIME_IMAGE_TAG'] = runtime_tag

        # Flatten nested dict into list of KEY=VALUE strings, for example:
        # EVC:
        #   enable: false
        #   snmp_port: null
        #           |
        #           v
        # EVC_ENABLE=false
        def flatten(d: Dict, prefix: str = "") -> List[str]:
            items = []
            for k, v in d.items():
                key = f"{prefix}{k.upper()}"
                if isinstance(v, dict):
                    items.extend(flatten(v, f"{key}_"))
                else:
                    if isinstance(v, list):
                        val = ",".join(str(item) for item in v)
                    else:
                        val = "" if v is None else str(v)
                    items.append(f"{key}={val}")
            return items

        content = "\n".join(flatten(base) + flatten(settings))
        env_path = self.tmp_dir / env_filename
        env_path.write_text(content)
        print(f"Generated {env_path}")
        return str(env_path)

    def _generate_cdasim_runtime_with_ns3_image(
        self, cdasim: Dict[str, Any]
    ) -> str:
        """Create a runtime.json with the configured NS-3 federate image.

        Args:
            cdasim: The CDASim deployment entry from ``env_settings``. Its
                ``settings`` mapping must define ``NS3_FEDERATE_IMAGE``.

        Returns:
            The absolute path to ``tmp/cdasim-runtime.json``. The same path is
            added to the CDASim settings as ``CDASIM_RUNTIME_FILE`` for the
            runtime Compose override.

        Raises:
            FileNotFoundError: If the repository runtime template is missing.
            ValueError: If ``settings`` is not a mapping,
                ``NS3_FEDERATE_IMAGE`` is empty, the template does not contain
                a ``federates`` list, or it does not contain exactly one
                federate whose ``id`` is ``ns3``.
            json.JSONDecodeError: If the runtime template is not valid JSON.

        Example:
            With ``NS3_FEDERATE_IMAGE`` set to
            ``usdotfhwastoldev/ns3-federate:develop-dsrc``, this method writes a
            generated runtime file containing that value in the NS-3
            federate's ``dockerImage`` field.
        """

        settings = cdasim.get("settings")
        if not isinstance(settings, dict):
            raise ValueError("CDASim settings must be a mapping")
        ns3_image = settings.get("NS3_FEDERATE_IMAGE")
        if not isinstance(ns3_image, str) or not ns3_image.strip():
            raise ValueError("NS3_FEDERATE_IMAGE must be a non-empty string")

        with CDASIM_RUNTIME_TEMPLATE_PATH.open(
            "r", encoding="utf-8"
        ) as runtime_template:
            runtime_config = json.load(runtime_template)

        federates = runtime_config.get("federates")
        if not isinstance(federates, list):
            raise ValueError("CDASim runtime template has no federates list")

        ns3_federates = [
            federate
            for federate in federates
            if isinstance(federate, dict) and federate.get("id") == "ns3"
        ]
        if len(ns3_federates) != 1:
            raise ValueError(
                "CDASim runtime template must contain exactly one ns3 federate"
            )

        ns3_federates[0]["dockerImage"] = ns3_image.strip()
        runtime_path = self.tmp_dir / "cdasim-runtime.json"
        runtime_path.write_text(
            json.dumps(runtime_config, indent=4) + "\n",
            encoding="utf-8",
        )
        settings["CDASIM_RUNTIME_FILE"] = str(runtime_path)
        print(f"Generated {runtime_path}")
        return str(runtime_path)

    def _generate_cdasim_network_override(
        self,
        vehicles: List[Dict[str, Any]],
        streets: List[Dict[str, Any]],
    ) -> Optional[str]:
        """Attach CDASim to vehicle networks and the shared street network."""

        service_networks = {}
        networks = {}
        for index, vehicle in enumerate(vehicles, 1):
            settings = vehicle["settings"]
            network_key = f"vehicle_private_{index}"
            service_networks[network_key] = {}
            networks[network_key] = {
                "external": True,
                "name": settings["PRIVATE_NETWORK_NAME"],
            }

        if streets:
            network_key = "streets_shared"
            street_network_name = streets[0]["settings"][
                "STREET_NETWORK_NAME"
            ]
            service_networks[network_key] = {
                "aliases": ["cdasim"],
            }
            evc_aliases = [
                street["settings"]["EVC_SIM_HOST"]
                for street in streets
                if "EVC_SIM_HOST" in street["settings"]
            ]
            evc_network = {}
            if evc_aliases:
                evc_network["aliases"] = evc_aliases
            networks[network_key] = {
                "external": True,
                "name": street_network_name,
            }

        if not networks:
            return None

        services = {"cdasim": {"networks": service_networks}}
        if streets:
            services["econolite-virtual-controller"] = {
                "networks": {"streets_shared": evc_network}
            }

        override_path = self.tmp_dir / "cdasim-private-networks.yml"
        override_path.write_text(
            yaml.safe_dump(
                {
                    "services": services,
                    "networks": networks,
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return str(override_path)

    @staticmethod
    def _replace_cloud_init_param(xml: str, name: str, value: str) -> str:
        """Replace one CARMA Cloud servlet init-param value."""

        pattern = re.compile(
            rf"(<param-name>\s*{re.escape(name)}\s*</param-name>\s*"
            rf"<param-value>).*?(</param-value>)",
            re.DOTALL,
        )
        updated, count = pattern.subn(rf"\g<1>{value}\g<2>", xml, count=1)
        if count != 1:
            raise ValueError(f"CARMA Cloud web.xml has no {name!r} init-param")
        return updated

    def _generate_cloud_web_xml_override(
        self, cloud: Dict[str, Any]
    ) -> Optional[str]:
        """Generate a DNS-based CARMA Cloud simulation configuration."""

        configured_source = cloud.get("WEB_XML_FILE")
        if configured_source:
            source = Path(self.compose.resolve_path(configured_source))
        elif cloud.get("COMPOSE_FILE"):
            compose_path = Path(self.compose.resolve_path(cloud["COMPOSE_FILE"]))
            source = compose_path.parent / "carma-cloud-config" / "web.xml"
        else:
            # A config-image deployment may already provide its own DNS-ready
            # web.xml and can supply an explicit WEB_XML_FILE when it does not.
            return None

        if not source.is_file():
            raise FileNotFoundError(f"CARMA Cloud web.xml not found: {source}")

        settings = cloud["settings"]
        xml = source.read_text(encoding="utf-8")
        xml = self._replace_cloud_init_param(xml, "simulation", "true")
        xml = self._replace_cloud_init_param(
            xml, "ambassador", settings["CDASIM_SIM_HOST"]
        )
        callback = (
            f"http://{settings['CARMA_CLOUD_SIM_HOST']}:8080/"
            "carmacloud/simulation"
        )
        xml = self._replace_cloud_init_param(xml, "url", callback)

        generated_xml = self.tmp_dir / "carma-cloud-web.xml"
        generated_xml.write_text(xml, encoding="utf-8")
        override_path = self.tmp_dir / "carma-cloud-web-override.yml"
        override_path.write_text(
            yaml.safe_dump(
                {
                    "services": {
                        "carma-cloud": {
                            "volumes": [
                                {
                                    "type": "bind",
                                    "source": str(generated_xml),
                                    "target": (
                                        "/opt/tomcat/webapps/carmacloud/ROOT/"
                                        "WEB-INF/web.xml"
                                    ),
                                    "read_only": True,
                                }
                            ]
                        }
                    }
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return str(override_path)

    def _generate_vehicle_v2x_override(
        self, vehicle: Dict[str, Any], index: int
    ) -> str:
        """Generate instance-specific V2X parameters for one vehicle."""

        component = vehicle.get("COMPONENT", "platform")
        settings = vehicle["settings"]
        if component == "messenger":
            service = "messenger-v2x-ros-driver"
            address = settings["CDASIM_MESSENGER_HOST"]
            radio_port, listening_port = 3601, 3501
        else:
            service = "v2x-ros-driver"
            address = settings["CDASIM_VEHICLE_HOST"]
            radio_port, listening_port = 1516, 2500

        prefix = f"{component}-v2x-{index}"
        params_path = self.tmp_dir / f"{prefix}-params.yaml"
        params_path.write_text(
            yaml.safe_dump(
                {
                    "v2x_radio_address": address,
                    "v2x_radio_listening_port": radio_port,
                    "listening_port": listening_port,
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )

        override_path = self.tmp_dir / f"{prefix}-override.yml"
        override_path.write_text(
            yaml.safe_dump(
                {
                    "services": {
                        service: {
                            "volumes": [
                                {
                                    "type": "bind",
                                    "source": str(params_path),
                                    "target": self.V2X_PARAMS_TARGET,
                                    "read_only": True,
                                }
                            ]
                        }
                    }
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return str(override_path)

    def _scenario_entry(
        self,
        component: dict,
        compose_files: list[str],
        env_filename: str,
        project_directory: Path,
    ) -> dict:
        return {
            "PROJECT_NAME": component["PROJECT_NAME"],
            "compose_files": compose_files,
            "env_file": str(self.tmp_dir / env_filename),
            "project_directory": project_directory,
        }

    def _prepare_scenario_data(self) -> Dict:
        es = self.config['env_settings']
        scenario = []

        # CDASim
        cd = es['cdasim']
        compose_files = self.compose.compose_files(cd, cd['PROJECT_NAME'])
        private_network_override = self._generate_cdasim_network_override(
            es.get('vehicles', []),
            es.get('streets', []),
        )
        if private_network_override:
            compose_files.append(private_network_override)
        scenario.append(
            self._scenario_entry(cd, compose_files, ".env.cdasim", self.tmp_dir)
        )

        # CARMA Cloud
        cloud = es.get('carma_cloud')
        if cloud:
            compose_files = self.compose.compose_files(
                cloud, cloud['PROJECT_NAME']
            )
            cloud_web_override = self._generate_cloud_web_xml_override(cloud)
            if cloud_web_override:
                compose_files.append(cloud_web_override)
            scenario.append(
                self._scenario_entry(
                    cloud, compose_files, ".env.carma_cloud", self.tmp_dir
                )
            )

        # Vehicles
        for i, v in enumerate(es.get('vehicles', []), 1):
            compose_files = self.compose.compose_files(v, v['PROJECT_NAME'])
            compose_files.append(self._generate_vehicle_v2x_override(v, i))
            scenario.append(
                self._scenario_entry(
                    v,
                    compose_files,
                    f".env.vehicle_{i}",
                    self.tmp_dir / f"config-{v['PROJECT_NAME']}",
                )
            )

        # Streets
        for i, s in enumerate(es.get('streets', []), 1):
            compose_files = self.compose.compose_files(s, s['PROJECT_NAME'])
            compose_files.append(str(STREET_NETWORK_OVERRIDE_PATH))
            scenario.append(
                self._scenario_entry(
                    s, compose_files, f".env.street_{i}", self.tmp_dir
                )
            )

        return {
            'scenario': scenario,
            'networks': es.get('runner_networks', []),
            'config_containers': list(self.compose.config_containers.values()),
            'scenario_resources': self.config.get('scenario_resources', {}),
            'temp_dir': str(self.tmp_dir)
        }

    # --------------------------------------------------------------------- #
    # 5. Generate sim_start.sh
    # --------------------------------------------------------------------- #
    def generate_start_script(self) -> str:
        with open(self.start_template, 'r') as f:
            tmpl = Template(f.read())
        content = tmpl.render(shell_quote=shlex.quote, **self.data)

        start_path = self.tmp_dir / "sim_start.sh"
        start_path.write_text(content)
        start_path.chmod(0o755)
        print(f"Generated {start_path}")
        return str(start_path)

    # --------------------------------------------------------------------- #
    # 6. Generate sim_stop.sh
    # --------------------------------------------------------------------- #
    def generate_stop_script(self) -> str:
        with open(self.stop_template, 'r') as f:
            tmpl = Template(f.read())
        content = tmpl.render(**self.data)

        stop_path = self.tmp_dir / "sim_stop.sh"
        stop_path.write_text(content)
        stop_path.chmod(0o755)
        print(f"Generated {stop_path}")
        return str(stop_path)

    # --------------------------------------------------------------------- #
    # 7. Public generate() — ONE CALL to _prepare_scenario_data()
    # --------------------------------------------------------------------- #
    def generate(self) -> Dict[str, str]:
        if not self.config:
            self.load_config()

        es = self.config['env_settings']

        self.resource_manager.prepare_infrastructure(
            self.config.get("scenario_resources", {}), es
        )

        cdasim_settings = es['cdasim'].get('settings', {})
        if 'NS3_FEDERATE_IMAGE' in cdasim_settings:
            self._generate_cdasim_runtime_with_ns3_image(es['cdasim'])

        # Generate .env files
        self.generate_env_file(
            '.env.cdasim',
            self.resource_manager.cdasim_env_settings(
                es['cdasim'], self.config.get("scenario_resources", {})
            ),
        )
        if es.get('carma_cloud'):
            self.generate_env_file('.env.carma_cloud', es['carma_cloud'])
        for i, v in enumerate(es.get('vehicles', []), 1):
            self.generate_env_file(f'.env.vehicle_{i}', v)
        for i, s in enumerate(es.get('streets', []), 1):
            self.generate_env_file(f'.env.street_{i}', s)

        # ONE CALL: prepare scenario data
        self.data = self._prepare_scenario_data()
        print("Prepared scenario data (cached)")

        # Generate scripts using cached data
        start_script = self.generate_start_script()
        stop_script = self.generate_stop_script()

        return {
            'start_script': start_script,
            'stop_script': stop_script
        }


# # ------------------------------------------------------------------------- #
# # CLI entry point
# # ------------------------------------------------------------------------- #
# if __name__ == "__main__":
#     gen = ScenarioGenerator()
#     scripts = gen.generate()
#     print(f"\nStart: bash {scripts['start_script']}")
#     print(f"Stop:  bash {scripts['stop_script']}")
