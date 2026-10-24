"""A ground station for the tutorial's vehicle, written with pymavlink (not with WireForge).

It listens on UDP port 14550, waits for the vehicle's HEARTBEAT, sends COMMAND_LONG (arm) and checks
that COMMAND_ACK, ATTITUDE and VFR_HUD arrive. Run it while drone_sim runs:
    python ground_station.py
Exit code 0 when everything arrived.
"""

from __future__ import annotations

import os
import sys
import time

os.environ["MAVLINK20"] = "1"  # MAVLink 2, before pymavlink is imported
from pymavlink import mavutil

TIMEOUT = 8.0
ARM_DISARM = 400


def main() -> int:
    """Talk to the vehicle; return 0 when every expected message arrived."""
    station = mavutil.mavlink_connection("udpin:127.0.0.1:14550", source_system=255)
    heartbeat = station.wait_heartbeat(timeout=TIMEOUT)
    if heartbeat is None:
        print("ground station: no HEARTBEAT")
        return 1
    print(f"ground station: HEARTBEAT from system {station.target_system}, type {heartbeat.type}")
    station.mav.heartbeat_send(6, 8, 0, 0, 4)  # a ground station (MAV_TYPE_GCS, no autopilot)
    station.mav.command_long_send(1, 1, ARM_DISARM, 0, 1, 0, 0, 0, 0, 0, 0)
    seen: set[str] = set()
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline and not {"COMMAND_ACK", "ATTITUDE", "VFR_HUD"} <= seen:
        message = station.recv_match(type=["COMMAND_ACK", "ATTITUDE", "VFR_HUD"], timeout=1)
        if message is None:
            continue
        kind = message.get_type()
        if kind not in seen:
            print(f"ground station: {kind} {message.to_dict()}")
        seen.add(kind)
    missing = {"COMMAND_ACK", "ATTITUDE", "VFR_HUD"} - seen
    print("ground station: all messages arrived" if not missing else f"missing: {sorted(missing)}")
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
