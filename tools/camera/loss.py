"""loss.py EPISODE.mcap: per camera frame stream, frames, missing by color number, gaps; and
where the camera's diagnostics say they went (skipped/drops/replaced, queue_max), and the
device host's dropped readings."""
import sys, collections, json
from pathlib import Path
import numpy as np
from xpisode_format.reader import EpisodeReader, EventMessage
p = Path(sys.argv[1])
with EpisodeReader(p) as r:
    seqs = collections.defaultdict(list); times = collections.defaultdict(list); disc = collections.defaultdict(list); lat = collections.defaultdict(list)
    diags = collections.defaultdict(list); hostdrop = collections.Counter()
    for m in r.messages():
        t = m.channel.topic
        if isinstance(m, EventMessage):
            if m.kind == "device.diagnostics": diags[t.split("/")[1]].append(m.data)
            continue
        if t.endswith("/frame"):
            seqs[t].append(int(m.value("color_sequence"))); times[t].append(m.log_time)
            disc[t].append(int(m.value("discarded_frames"))); lat[t].append(float(m.value("latency_ms")))
        elif t.endswith("/host") and "dropped" in [f.name for f in m.stream.fields]:
            hostdrop[t.split("/")[1]] += int(m.value("dropped"))
    for t in sorted(seqs):
        s = np.array(seqs[t]); d = np.diff(s); span = s[-1] - s[0] + 1; miss = int((d - 1).clip(0).sum())
        dur = (times[t][-1] - times[t][0]) / 1e9
        gi = d[d > 1] - 1
        cam = t.split("/")[1]; dg = diags.get(cam, [])
        where = ""
        if dg and "skipped" in dg[-1]:
            a, b = dg[0], dg[-1]
            where = (f" | diag over {len(dg)}x10s: skipped {b['skipped']-a['skipped']} replaced {b['replaced']} drops {b['drops']}"
                     f" queue_max {max(x['queue_max'] for x in dg)}")
        print(f"{t:24s} n={len(s)} dur={dur:.1f}s missing={miss} ({100*miss/span:.2f}%) gaps={len(gi)} sizes={collections.Counter(gi.tolist()).most_common(3)} discarded={disc[t][-1]-disc[t][0]} hostdrop={hostdrop[cam]} lat_p50={np.median(lat[t]):.1f} p99={np.percentile(lat[t],99):.1f}{where}")
