
from setuptools import setup, find_packages
from glob import glob
import os

package_name = 'dws_ros2'

setup(
    name=package_name,
    version='0.1.1',

    packages=find_packages(),

    data_files=[

        (
            'share/ament_index/resource_index/packages',
            ['resource/' + package_name]
        ),

        (
            'share/' + package_name,
            ['package.xml']
        ),

        (
            os.path.join(
                'share',
                package_name,
                'launch'
            ),
            glob('launch/*')
        ),

        (
            os.path.join(
                'share',
                package_name,
                'config'
            ),
            glob('config/*')
        ),

        (
            os.path.join(
                'share',
                package_name,
                'hmi'
            ),
            [f for f in glob('hmi/*') if os.path.isfile(f)]
        ),
        (
            os.path.join(
                'share',
                package_name,
                'hmi',
                'css'
            ),
            glob('hmi/css/*')
        ),
        (
            os.path.join(
                'share',
                package_name,
                'hmi',
                'js'
            ),
            glob('hmi/js/*')
        ),

        # ------------------------------------------------
        # RealSense calibration assets
        # ------------------------------------------------

        (
            os.path.join(
                'share',
                package_name,
                'realsense_vision',
                'calibration'
            ),
            glob(
                'dws_ros2/realsense_vision/calibration/*.npy'
            )
        ),
    ],

    install_requires=[
        'setuptools',
        'websockets',
        'aiohttp',
        'numpy',
        'opencv-python',
        'open3d',
        'pyrealsense2',
        'pyserial',
        'scikit-learn',
        'ultralytics',
        'evdev',
        'pyyaml',
        'fastapi',
        'uvicorn',
        'reportlab',
        'bcrypt',
    ],

    zip_safe=True,

    maintainer='Ashmit Singh',
    maintainer_email='ashmitsingh10101@gmail.com',

    description='Dynamic Weighing System ROS2 package',
    license='MIT',

    entry_points={
        'console_scripts': [

            'vision_node=dws_ros2.vision_node:main',
            'vision_node_realsense=dws_ros2.vision_node_realsense:main',
            'scale_node=dws_ros2.scale_node:main',
            'barcode_node=dws_ros2.barcode_node:main',
            'orchestrator_node=dws_ros2.orchestrator_node:main',
            'database_node=dws_ros2.database_node:main',
            'sync_worker_node=dws_ros2.sync_worker_node:main',
            'hmi_bridge_node=dws_ros2.hmi_bridge_node:main',
            'dws_simulate=dws_ros2.simulate:main',
            'run_dws=dws_ros2.run_dws:main',

        ],
    },

    options={
        'install': {
            'install_scripts':
            '$base/lib/' + package_name
        }
    }
)
