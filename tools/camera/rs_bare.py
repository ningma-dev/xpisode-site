"""Bare librealsense: this station's D405s as its file opens them (each camera's serial, size,
fps, exposure and frame fields from its xpisode_realsense:D405 device), a callback that only
notes frame numbers. Counts gaps. The control group for the station's camera frame loss.
Run it through scripts/camera_baseline.sh, which names the station file:
    rs_bare.py SECONDS STATION_FILE [--copy] [--noglobal] [--keeper]"""
import sys, time, collections, json, tomllib
import pyrealsense2 as rs
args = [a for a in sys.argv[1:] if not a.startswith("--")]
if len(args) != 2: sys.exit(__doc__)
secs = float(args[0]); copy = "--copy" in sys.argv
STREAMS = {"color": (rs.stream.color, 0, rs.format.rgb8),
           "infrared_left": (rs.stream.infrared, 1, rs.format.y8),
           "infrared_right": (rs.stream.infrared, 2, rs.format.y8)}
CAMERAS = {}
for device in tomllib.load(open(args[1], "rb")).get("devices", []):
    command = device.get("command", [])
    if "xpisode_realsense:D405" in command:
        c = json.loads(command[command.index("--config") + 1])
        c = {"width": 848, "height": 480, "fps": 30, **c}  # xpisode_realsense.D405's defaults
        unknown = set(c.get("frame_fields", ["depth"])) - set(STREAMS)
        if unknown: sys.exit(f'{device["name"]}: rs_bare reads only {sorted(STREAMS)}, not {sorted(unknown)}')
        CAMERAS[device["name"]] = c
if not CAMERAS: sys.exit(f"{args[1]} names no D405")
SERIALS = [c["serial"] for c in CAMERAS.values()]
if any(s.startswith("FILL") for s in SERIALS): sys.exit(f"{args[1]}: camera serials still FILL")
CONFIG = {c["serial"]: c for c in CAMERAS.values()}
for name, c in CAMERAS.items():
    print(name, c["serial"], f'{c["width"]}x{c["height"]}', c["fps"], "fps", c.get("exposure_us"), "us", c["frame_fields"])
seen = {s: collections.defaultdict(list) for s in SERIALS}
pipes = []; devs = []
for s in SERIALS:
    c = CONFIG[s]; cfg = rs.config(); cfg.enable_device(s)
    for field in c["frame_fields"]:
        stream, index, fmt = STREAMS[field]
        cfg.enable_stream(stream, index, c["width"], c["height"], fmt, c["fps"])
    p = rs.pipeline()
    def cb(f, s=s):
        for fr in f.as_frameset():
            pr = fr.get_profile(); seen[s][(pr.stream_type(), pr.stream_index())].append(fr.get_frame_number())
            if copy: bytes(fr.get_data())
    prof = p.start(cfg, cb)
    for sensor in prof.get_device().query_sensors():
        if sensor.supports(rs.option.exposure):
            if c.get("exposure_us") is not None:
                sensor.set_option(rs.option.enable_auto_exposure, 0); sensor.set_option(rs.option.exposure, float(c["exposure_us"]))
    dev = prof.get_device()
    if "--noglobal" in sys.argv:
        for sensor in dev.query_sensors():
            if sensor.supports(rs.option.global_time_enabled): sensor.set_option(rs.option.global_time_enabled, 0)
    devs.append(dev)
    pipes.append(p)
time.sleep(3)
for s in SERIALS:
    for k in seen[s]: seen[s][k].clear()
if "--keeper" in sys.argv:
    end = time.monotonic() + secs
    while time.monotonic() < end:
        for d in devs: d.get_device_time_ms()
        time.sleep(1.0)
else:
    time.sleep(secs)
for p in pipes: p.stop()
for s in SERIALS:
    for k, v in seen[s].items():
        u = sorted(set(v)); missing = (u[-1] - u[0] + 1) - len(u)
        print(s, k, "n", len(v), "unique", len(u), "span", u[-1] - u[0] + 1, "missing", missing, f"{100*missing/(u[-1]-u[0]+1):.1f}%")
