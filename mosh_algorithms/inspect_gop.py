import os
import csv
import av
from av.video.frame import PictureType  # to map int -> enum safely

def _fmt_time(seconds):
    if seconds is None:
        return ""
    return f"{seconds:.6f}"

def _pict_name(frame):
    """
    Return pict type name ('I','P','B','SI','SP','BI', or '?'),
    handling cases where PyAV gives an int or an enum.
    """
    pt = getattr(frame, "pict_type", None)
    if pt is None:
        return "?"
    try:
        # If it's already an enum with .name
        name = getattr(pt, "name", None)
        if name:
            return name
        # If it's an int, coerce to enum then read .name
        return PictureType(int(pt)).name
    except Exception:
        # Last resort: string-ify and take the tail token
        s = str(pt)
        if s and s[-1].isalpha():
            return s.split(".")[-1]
        return "?"

def process(input_path: str, output_path: str, alpha=0.85, block=16, radius=8,
            gop=250, codec="libx264", verbose=False):
    """
    Inspect GOP structure and write a CSV with one row per decoded frame.
    Also prints a concise summary to stdout.

    Columns: index, pts_sec, pict_type, keyframe, idr_guess, width, height
    """
    input_path = os.path.abspath(input_path)

    # Resolve CSV path (force .csv even if caller gave .mp4/.txt/etc.)
    root, ext = os.path.splitext(os.path.abspath(output_path))
    csv_path = root + ".csv" if ext.lower() != ".csv" else os.path.abspath(output_path)

    with av.open(input_path) as container:
        v = next(s for s in container.streams if s.type == "video")
        avg_rate = v.average_rate
        tb_stream = v.time_base

        rows = []
        counts = {"I": 0, "P": 0, "B": 0, "other": 0, "key": 0, "idr": 0}

        for idx, frame in enumerate(container.decode(video=0)):
            pict = _pict_name(frame)  # robust name
            kind = pict if pict in ("I", "P", "B") else "other"

            key = bool(getattr(frame, "key_frame", False))
            idr = key and pict in ("I", "SI")  # heuristic

            tb = getattr(frame, "time_base", None) or tb_stream
            if frame.pts is not None and tb is not None:
                t = float(frame.pts * tb)
            elif avg_rate:
                t = float(idx) / float(avg_rate)
            else:
                t = None

            rows.append([idx, t, pict, int(key), int(idr), frame.width, frame.height])

            counts[kind] = counts.get(kind, 0) + 1
            if key:
                counts["key"] += 1
            if idr:
                counts["idr"] += 1

    # Write CSV
    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["index", "pts_sec", "pict_type", "keyframe", "idr_guess", "width", "height"])
        for idx, t, pict, key, idr, wdt, hgt in rows:
            w.writerow([idx, _fmt_time(t), pict, key, idr, wdt, hgt])

    total = len(rows)
    i_count = counts.get("I", 0)
    p_count = counts.get("P", 0)
    b_count = counts.get("B", 0)
    o_count = counts.get("other", 0)
    print(f"[GOP] Frames: {total} | I: {i_count} | P: {p_count} | B: {b_count} | other: {o_count} | key: {counts['key']} | idr~: {counts['idr']}")
    print(f"[GOP] CSV written to: {csv_path}")
