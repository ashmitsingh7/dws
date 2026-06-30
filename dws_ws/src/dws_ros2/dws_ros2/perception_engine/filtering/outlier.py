"""
filtering/outlier.py

Thin convenience re-export of Stage 6 (point cloud cleaning) at the
`filtering` layer, matching the architecture diagram's
filtering/outlier.py path. The actual implementation lives in
geometry/pointcloud.py since it's a general point-cloud operation reused
by multiple stages.
"""

from ..geometry.pointcloud import (
    remove_statistical_outlier,
    remove_radius_outlier,
    clean_point_cloud,
)

__all__ = ["remove_statistical_outlier", "remove_radius_outlier", "clean_point_cloud"]
