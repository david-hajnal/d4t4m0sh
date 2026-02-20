import os
import json
import subprocess
from itertools import combinations


def _run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        err = (p.stderr or "").strip()
        out = (p.stdout or "").strip()
        if err:
            msg = err
        elif out:
            msg = out
        else:
            msg = f"ffmpeg failed: {' '.join(cmd)}"
        raise RuntimeError(msg)
    return p.stdout


def _probe_dims(path: str):
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "json",
        path,
    ]
    out = _run(cmd)
    data = json.loads(out)
    stream = data["streams"][0]
    return int(stream["width"]), int(stream["height"])


def _probe_duration(path: str):
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        path,
    ]
    out = _run(cmd)
    data = json.loads(out)
    return float(data["format"]["duration"])

def _has_video_stream(path: str) -> bool:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "json",
        path,
    ]
    try:
        out = _run(cmd)
    except Exception:
        return False
    data = json.loads(out)
    streams = data.get("streams", [])
    return len(streams) > 0


VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm")


def _scan_videos(dirpath: str, verbose: bool = False):
    if not os.path.isdir(dirpath):
        return []
    out = []
    for name in sorted(os.listdir(dirpath)):
        p = os.path.join(dirpath, name)
        if os.path.isfile(p) and name.lower().endswith(VIDEO_EXTS):
            ap = os.path.abspath(p)
            if _has_video_stream(ap):
                out.append(ap)
            elif verbose:
                print(f"[WARN] Skipping non-video file: {ap}")
    return out


def _build_filtergraph(inputs, w, h, max_dur, mode, opacity):
    parts = []
    for i, _ in enumerate(inputs):
        v = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2"
        pad = max(0.0, max_dur - _probe_duration(inputs[i]))
        if pad > 0:
            v += f",tpad=stop_mode=clone:stop_duration={pad}"
        v += ",format=rgba"
        parts.append(f"[{i}:v]{v}[v{i}]")

    w0 = max(0.0, min(1.0, 1.0 - opacity))
    w1 = max(0.0, min(1.0, opacity))
    cur = "v0"
    for i in range(1, len(inputs)):
        parts.append(f"[{cur}]colorchannelmixer=aa={w0}[cw{i}]")
        parts.append(f"[v{i}]colorchannelmixer=aa={w1}[nw{i}]")
        parts.append(f"[cw{i}][nw{i}]blend=all_mode={mode}:shortest=0[b{i}]")
        cur = f"b{i}"

    parts.append(f"[{cur}]format=yuv420p[v]")
    return ";".join(parts)


def _output_sequence_paths(output_path: str, count: int):
    out_dir = os.path.dirname(output_path) or "."
    ext = os.path.splitext(output_path)[1] or ".mp4"
    width = max(4, len(str(count)))
    paths = []
    for i in range(count):
        name = f"{i+1:0{width}d}{ext}"
        paths.append(os.path.join(out_dir, name))
    return paths


def _run_blend(inputs, output_path, mode, opacity, gop, codec, verbose):
    w, h = _probe_dims(inputs[0])
    max_dur = max(_probe_duration(p) for p in inputs)
    filtergraph = _build_filtergraph(inputs, w, h, max_dur, mode, opacity)

    cmd = ["ffmpeg", "-y"]
    for p in inputs:
        cmd.extend(["-i", p])
    cmd.extend([
        "-filter_complex", filtergraph,
        "-map", "[v]",
        "-an",
        "-c:v", codec,
        "-g", str(int(gop)),
        output_path,
    ])

    if not verbose:
        cmd.insert(1, "-hide_banner")
        cmd.insert(2, "-loglevel")
        cmd.insert(3, "error")

    _run(cmd)


def process(
    input_path: str,
    output_path: str,
    alpha=0.85,
    block=16,
    radius=8,
    gop=250,
    codec="libx264",
    verbose=False,
    image=None,
    img_dur=None,
    kb_mode=None,
    postcut=6,
    blend_mode="overlay",
    opacity=0.5,
    descartes=False,
    videosrc="videosrc",
):
    """
    Double exposure blend of two clips using FFmpeg.
    Supported modes: overlay, add, subtract, darken, lighten.
    """
    mode_map = {
        "overlay": "overlay",
        "add": "addition",
        "subtract": "subtract",
        "darken": "darken",
        "lighten": "lighten",
    }
    if blend_mode not in mode_map:
        raise ValueError(f"Unsupported blend mode: {blend_mode}")

    try:
        opacity = float(opacity)
    except Exception:
        raise ValueError("opacity must be a number between 0 and 1")
    if not (0.0 <= opacity <= 1.0):
        raise ValueError("opacity must be between 0 and 1")

    if descartes:
        inputs = _scan_videos(videosrc, verbose=verbose)
        if len(inputs) < 2:
            raise ValueError(f"Need at least 2 clips in '{videosrc}' for descartes mode")
        pairs = list(combinations(range(len(inputs)), 2))
        output_paths = _output_sequence_paths(output_path, len(pairs))
        for (i, j), out_path in zip(pairs, output_paths):
            _run_blend([inputs[i], inputs[j]], out_path, mode_map[blend_mode], opacity, gop, codec, verbose)
        return

    inputs = [p.strip() for p in input_path.split(",") if p.strip()]
    if len(inputs) != 2:
        raise ValueError("double_exposure requires exactly two input clips")

    a, b = inputs
    if not os.path.exists(a) or not os.path.exists(b):
        missing = [p for p in (a, b) if not os.path.exists(p)]
        raise FileNotFoundError(f"Missing input(s): {', '.join(missing)}")

    _run_blend([a, b], output_path, mode_map[blend_mode], opacity, gop, codec, verbose)
