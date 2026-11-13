"""Bare librealsense: the three D405s as the station opens them (640x480 color rgb8 + both
IR y8, 30 fps, exposure 32 ms), a callback that only notes frame numbers. Counts gaps.
rs_bare.py SECONDS SERIAL... [--copy] [--noglobal] [--keeper]
The serials are this machine's cameras (its robots/<station>/station.toml)."""
import sys, time, threading, collections
import pyrealsense2 as rs
secs = float(sys.argv[1]); copy = "--copy" in sys.argv
SERIALS = [a for a in sys.argv[2:] if not a.startswith("--")]
if not SERIALS: sys.exit(__doc__)
seen = {s: collections.defaultdict(list) for s in SERIALS}
pipes = []; devs = []
for s in SERIALS:
    cfg = rs.config(); cfg.enable_device(s)
    cfg.enable_stream(rs.stream.color, 640, 480, rs.format.rgb8, 30)
    cfg.enable_stream(rs.stream.infrared, 1, 640, 480, rs.format.y8, 30)
    cfg.enable_stream(rs.stream.infrared, 2, 640, 480, rs.format.y8, 30)
    p = rs.pipeline()
    def cb(f, s=s):
        for fr in f.as_frameset():
            pr = fr.get_profile(); seen[s][(pr.stream_type(), pr.stream_index())].append(fr.get_frame_number())
            if copy: bytes(fr.get_data())
    prof = p.start(cfg, cb)
    for sensor in prof.get_device().query_sensors():
        if sensor.supports(rs.option.exposure):
            sensor.set_option(rs.option.enable_auto_exposure, 0); sensor.set_option(rs.option.exposure, 32000.0)
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
