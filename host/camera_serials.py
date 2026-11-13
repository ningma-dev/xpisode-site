#!/usr/bin/env python3
"""Read-only: the RealSense cameras plugged in now, by the serial the station file needs.

    .venv/bin/python host/camera_serials.py            one line per camera
    .venv/bin/python host/camera_serials.py --serials  just the station-file serials

A D405 has two serials. The one the station file needs (``"serial"``) is librealsense's camera
serial, printed on the label. The USB descriptor carries another one (librealsense calls it the
ASIC serial); that is what lsusb and host/xhci_list.sh show. This lists both, with the USB port.
It only enumerates: no stream is started.
"""

import sys

import pyrealsense2 as rs


def main() -> int:
    rows = []
    for device in rs.context().query_devices():
        def info(key: rs.camera_info) -> str:
            return device.get_info(key) if device.supports(key) else "?"

        port = info(rs.camera_info.physical_port)
        parts = [part for part in port.split("/") if part[:1].isdigit() and "-" in part and ":" not in part]
        usb = parts[-1] if parts else "?"
        rows.append((info(rs.camera_info.serial_number), info(rs.camera_info.asic_serial_number), usb))
    if sys.argv[1:] == ["--serials"]:
        for serial, _, _ in rows:
            print(serial)
        return 0
    print(f"{'serial (station file)':<24}{'usb serial':<18}usb")
    for serial, asic, usb in sorted(rows, key=lambda row: row[2]):
        print(f"{serial:<24}{asic:<18}{usb}")
    if not rows:
        print("(no RealSense camera found)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
