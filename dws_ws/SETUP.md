# DWS Setup

## Create virtual environment

python3 -m venv .venv

## Activate

source .venv/bin/activate

## Install runtime dependencies

pip install -r requirements.txt

## Install development dependencies

pip install -r requirements-dev.txt

## Build ROS2 workspace

colcon build

source install/setup.bash

## Run DWS

ros2 run dws_ros2 run_dws
