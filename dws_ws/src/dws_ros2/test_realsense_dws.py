from dws_ros2.scale_node import ScaleNode

from dws_ros2.vision_node_realsense import (
    VisionNodeRealSense
)

from dws_ros2.orchestrator_node import (
    OrchestratorNode
)

import time
import cv2

# ============================================================
# INIT
# ============================================================

print("\n===================================")
print("INITIALIZING REALSENSE DWS SYSTEM")
print("===================================")

# ============================================================
# SCALE NODE
# ============================================================

print("\nInitializing scale node...")

scale_node = ScaleNode()

# ============================================================
# REALSENSE VISION NODE
# ============================================================

print("\nInitializing RealSense vision node...")

vision_node = VisionNodeRealSense()

# ============================================================
# ORCHESTRATOR
# ============================================================

print("\nInitializing orchestrator...")

orch = OrchestratorNode(

    scale_node=scale_node,

    vision_node=vision_node

)

# ============================================================
# START SYSTEM
# ============================================================

print("\n===================================")
print("STARTING DWS SYSTEM")
print("===================================\n")

orch.start()

# ============================================================
# MAIN LOOP
# ============================================================

try:

    while True:

        # ----------------------------------------------------
        # PROCESS FRAME
        # ----------------------------------------------------

        result = vision_node.process()

        # ----------------------------------------------------
        # INVALID RESULT
        # ----------------------------------------------------

        if result is None:

            print("\nNo result returned")

            time.sleep(0.03)

            continue

        # ----------------------------------------------------
        # EXTRACT DIMENSIONS
        # ----------------------------------------------------

        length = result.get(
            "length_mm",
            0.0
        )

        breadth = result.get(
            "breadth_mm",
            0.0
        )

        height = result.get(
            "height_mm",
            0.0
        )

        stable = result.get(
            "stable",
            False
        )

        valid = result.get(
            "valid",
            False
        )

        reason = result.get(
            "reason",
            "UNKNOWN"
        )

        locked = result.get(
            "locked",
            False
        )

        confidence = result.get(
            "confidence",
            0.0
        )

        # ----------------------------------------------------
        # PRINT STATUS
        # ----------------------------------------------------

        print("\n-----------------------------------")

        print(
            f"L={length:.1f} mm | "
            f"B={breadth:.1f} mm | "
            f"H={height:.1f} mm"
        )

        print(f"Stable    : {stable}")
        print(f"Locked    : {locked}")
        print(f"Valid     : {valid}")
        print(f"Confidence: {confidence:.2f}")

        if not valid:

            print(f"Reason    : {reason}")

        print("-----------------------------------")

        # ----------------------------------------------------
        # SHOW FRAME
        # ----------------------------------------------------

        frame = result.get(
            "annotated_frame",
            None
        )

        if frame is not None:

            cv2.imshow(
                "DWS Measurement",
                frame
            )

        # ----------------------------------------------------
        # SHOW MASK
        # ----------------------------------------------------

        mask = result.get(
            "mask",
            None
        )

        if mask is not None:

            cv2.imshow(
                "DWS Mask",
                mask
            )

        # ----------------------------------------------------
        # SHOW LOCK EVENT
        # ----------------------------------------------------

        if locked and valid:

            print(
                f"\nLOCKED | "
                f"L={length:.1f} "
                f"B={breadth:.1f} "
                f"H={height:.1f}"
            )

        # ----------------------------------------------------
        # EXIT
        # ----------------------------------------------------

        key = cv2.waitKey(1)

        if key == ord('q'):

            break

        time.sleep(0.03)

# ============================================================
# CLEANUP
# ============================================================

except KeyboardInterrupt:

    print("\nKeyboard interrupt received")

finally:

    print("\n===================================")
    print("STOPPING DWS SYSTEM")
    print("===================================\n")

    try:

        vision_node.shutdown()

    except Exception as e:

        print(
            "Vision cleanup error:",
            e
        )

    cv2.destroyAllWindows()

    print("Cleanup complete")