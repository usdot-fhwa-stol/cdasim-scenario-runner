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

#!/bin/bash
set -e

echo "### Building carma_planning_msgs for CDASim data analysis ###"

# Install system dependencies
echo "Installing system dependencies..."
sudo apt-get update && sudo apt-get install -y --no-install-recommends --no-upgrade \
    python3-pip \
    git && \
    sudo rm -rf /var/lib/apt/lists/*

# Install Python dependencies needed by the analysis scripts
echo "Installing Python dependencies..."
python3 -m pip install --no-cache-dir \
    numpy matplotlib scipy argcomplete mcap-ros2-support pytest

# Source ROS 2 Environment
echo "Sourcing ROS 2 environment..."
source /opt/ros/humble/setup.bash

# Clone ROS 2 message packages
echo "Cloning carma-msgs (develop branch)..."
mkdir -p ~/msgs_ws/src
cd ~/msgs_ws/src && git clone --depth 1 --branch develop https://github.com/usdot-fhwa-stol/carma-msgs.git

echo "Cloning autoware.ai (carma-develop branch) for autoware_msgs..."
mkdir -p ~/autoware_msgs_src
cd ~/autoware_msgs_src && git clone --depth 1 --branch carma-develop https://github.com/usdot-fhwa-stol/autoware.ai.git
ln -sf ~/autoware_msgs_src/autoware.ai/messages/autoware_msgs ~/msgs_ws/src/
ln -sf ~/autoware_msgs_src/autoware.ai/jsk_recognition/jsk_recognition_msgs ~/msgs_ws/src/

# Colcon Build
cd ~/msgs_ws
echo "Building carma_planning_msgs and its dependencies..."
colcon list
colcon build --symlink-install --packages-up-to carma_planning_msgs autoware_msgs

echo "### carma_planning_msgs build complete ###"
