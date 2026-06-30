"""
dws_launch.py
=============
Single-process DWS launcher (Build 005.1).

The DWS runtime uses an in-process SimBus (see dws_ros2/sim_bus.py). All
nodes must share one Python process — multi-node ROS2 launch is not supported.

Usage (real ROS2):
    ros2 launch dws_ros2 dws_launch.py

This launch file starts exactly one process:
    ros2 run dws_ros2 run_dws

For headless/systemd deployment, prefer deploy/run.sh instead.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, LogInfo
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():

    station_id_arg = DeclareLaunchArgument(
        "station_id",
        default_value="STATION_01",
        description=(
            "Station identifier (informational in this launch file — "
            "configure system.station_id in dws_params.yaml for runtime)"
        ),
    )

    station_id = LaunchConfiguration("station_id")

    dws_process = Node(
        package="dws_ros2",
        executable="run_dws",
        name="dws",
        output="screen",
    )

    return LaunchDescription([
        station_id_arg,
        LogInfo(msg=[
            "DWS+ single-process launch on station: ",
            station_id,
            " (all nodes run inside run_dws)",
        ]),
        dws_process,
    ])
