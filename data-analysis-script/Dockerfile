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

# ================================
# Base Image
# ================================
ARG DOCKER_ORG="usdotfhwastoldev"
ARG DOCKER_TAG="develop-humble"
FROM ${DOCKER_ORG}/carma-base:${DOCKER_TAG}

LABEL Description="CDASim data analysis image: cdasim log/mcap analyzers plus CARMA Platform control/regression analysis, with carma_planning_msgs built from source."

USER carma
WORKDIR /home/carma

# ================================
# Workspace Setup
# ================================
COPY --chown=carma:carma cdasim-scenario-runner/data-analysis-script/docker ./docker
COPY --chown=carma:carma cdasim-config/cdasim_data_analysis_scripts ./cdasim_data_analysis_scripts
COPY --chown=carma:carma carma-analytics-fotda/src/carma-platform ./carma-platform-scripts

# Build carma_planning_msgs
RUN /home/carma/docker/install.sh

# Automatically source ROS 2 and the built message workspace
RUN echo "source /opt/ros/humble/setup.bash" >> /home/carma/.bashrc && \
    echo "source /home/carma/msgs_ws/install/setup.bash" >> /home/carma/.bashrc

# ================================
# Final Settings
# ================================
ENV ROS_DISTRO=humble
SHELL ["/bin/bash", "-c"]
CMD ["bash"]
