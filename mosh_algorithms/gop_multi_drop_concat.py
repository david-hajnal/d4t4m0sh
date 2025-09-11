# mosh_algorithms/gop_multi_drop_concat.py
import os, json, shutil, subprocess, tempfile
import av  # for I-frame detection

def _run(cmd, verbose=False):
    loglevel = "info" if verbose else "error"
    if cmd and cmd[0] == "ffmpeg" and "-loglevel" not in cmd:
        cmd = cmd[:1] + ["-hide_banner", "-loglevel", loglevel] + cmd[1:]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg exit {p.returncode}\nCMD: {' '.join(cmd)}\n{p.stderr}")
    if verbose and p.stderr:
        print(p.stderr)
    return p

def _ffprobe(path):
    cmd = [
        "ffprobe","-v","error","-select_streams","v:0",
        "-show_entries","stream=width,height,avg_frame_rate",
        "-of","json", path
    ]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}:\n{p.stderr}")
    s = json.loads(p.stdout)["streams"][0]
    w, h = int(s["width"]), int(s["height"])
    fr = s.get("avg_frame_rate", "0/0")
    try:
        n, d = fr.split("/")
        fps = float(n) / float(d) if d != "0" else 30.0
    except Exception:
        fps = 30.0
    return w, h, fps

def _ffprobe_duration(path):
    p = subprocess.run(
        ["ffprobe","-v","error","-show_entries","format=duration","-of","json", path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe (duration) failed for {path}:\n{p.stderr}")
    data = json.loads(p.stdout)
    return float(data["format"]["duration"])

def _safe_fps_str(fps):
    if abs(fps - 23.976) < 0.05: return "24000/1001"
    if abs(fps - 29.97)  < 0.05: return "30000/1001"
    if abs(fps - 59.94)  < 0.1:  return "60000/1001"
    return str(max(1, int(round(fps))))

def _codec_default():
    return "h264_videotoolbox" if os.uname().sysname == "Darwin" else "libx264"

def _collect_iframes(path):
    idxs = []
    with av.open(path) as cont:
        v = next(s for s in cont.streams if s.type == "video")
        for i, frame in enumerate(cont.decode(video=0)):
            pict = getattr(frame, "pict_type", None)
            name = getattr(pict, "name", None) or str(pict).split(".")[-1]
            if name in ("I", "SI", "IDR") or bool(getattr(frame, "key_frame", False)):
                idxs.append(i)
    return idxs

def _build_select_not(drop_frame_numbers):
    if not drop_frame_numbers:
        return "1"
    parts = [f"eq(n\\,{n})" for n in sorted(set(drop_frame_numbers))]
    return f"not({' + '.join(parts)})"

def process(input_path: str, output_path: str, alpha=0.85, block=16, radius=8,
            gop=9999, codec=None, verbose=False, postcut=4):
    """
    Multi-clip datamosh (cross-clip smear):
      1) Normalize each clip to same WxH/FPS (AVI/MPEG-4, CFR, even dims).
      2) Concat with keyframes forced ONLY at the clip boundaries.
      3) Final pass: drop all I-frames except frame 0, plus N 'postcut' frames after each boundary I.
      4) Encode with long GOP, no B-frames. Use AVI/mpeg4 for strongest artifacts, or MP4/libx264.

    Args:
      postcut: how many frames to also drop immediately after each removed I (e.g., 3–8).
    """
    codec = codec or _codec_default()
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise RuntimeError("ffmpeg/ffprobe not found. Install via Homebrew: brew install ffmpeg")

    inputs = [s.strip() for s in input_path.split(",") if s.strip()]
    if not inputs:
        raise ValueError("Provide at least one input (comma-separated).")
    for f in inputs:
        if not os.path.exists(f):
            raise FileNotFoundError(f)

    wants_mp4 = output_path.lower().endswith((".mp4", ".mov", ".m4v"))

    # Baseline from first clip
    base_w, base_h, base_fps = _ffprobe(inputs[0])
    base_w = (base_w // 2) * 2
    base_h = (base_h // 2) * 2
    fps_str = _safe_fps_str(base_fps)

    mpeg4_gop = min(int(gop), 600)  # MPEG-4 cap

    with tempfile.TemporaryDirectory(prefix="datamosh_multi_v3_") as tmp:
        # 1) Normalize per clip (NO keyframe forcing here!)
        norm_paths = []
        for idx, src in enumerate(inputs):
            dst = os.path.join(tmp, f"norm_{idx:03d}.avi")
            _run([
                "ffmpeg","-y","-i", src,
                "-an",
                "-vf", f"scale=trunc(iw/2)*2:trunc(ih/2)*2,scale={base_w}:{base_h},fps={fps_str}",
                "-r", fps_str, "-vsync", "cfr",
                "-c:v","mpeg4","-qscale:v","8",
                "-g", str(mpeg4_gop), "-bf","0","-sc_threshold","0",
                "-pix_fmt","yuv420p",
                dst
            ], verbose=verbose)
            norm_paths.append(dst)

        # 2) Concat with boundary keyframes forced
        # Build boundary timestamps (start times of each subsequent clip)
        boundaries = []
        t = 0.0
        for i, p in enumerate(norm_paths):
            dur = _ffprobe_duration(p)
            if i == 0:
                t += dur
                continue
            boundaries.append(t)
            t += dur

        force_list = ",".join(["0"] + [f"{x:.6f}" for x in boundaries]) if boundaries else "0"

        concat_inputs = []
        for p in norm_paths:
            concat_inputs.extend(["-i", p])
        n = len(norm_paths)
        filtergraph = f"{''.join([f'[{i}:v]' for i in range(n)])}concat=n={n}:v=1:a=0"

        combined = os.path.join(tmp, "combined.avi")
        _run(["ffmpeg","-y"] + concat_inputs + [
            "-filter_complex", filtergraph,
            "-an",
            "-c:v","mpeg4","-qscale:v","2",
            "-g", str(mpeg4_gop), "-bf","0","-sc_threshold","0",
            "-pix_fmt","yuv420p",
            "-r", fps_str, "-vsync", "cfr",
            "-force_key_frames", force_list,   # <-- only here
            combined
        ], verbose=verbose)

        # 3) Build drop list: all I > 0, plus postcut frames after each
        i_frames = [i for i in _collect_iframes(combined) if i != 0]
        drop = []
        pc = max(0, int(postcut))
        for i in i_frames:
            drop.extend(i + k for k in range(0, pc + 1))
        select_expr = _build_select_not(drop)

        # 4) Final encode from combined with select (NO keyframe forcing here)
        if wants_mp4:
            enc = [
                "-c:v", codec,
                "-g", str(gop), "-bf","0","-sc_threshold","0",
                "-pix_fmt","yuv420p",
                "-movflags","+faststart",
                "-r", fps_str, "-vsync","cfr",
            ]
            if codec == "libx264":
                enc += ["-x264-params","keyint=9999:min-keyint=9999:scenecut=0:bframes=0:ref=1:weightp=0"]
        else:
            enc = [
                "-c:v","mpeg4","-qscale:v","3","-me_method","full","-me_range","32",
                "-mbd","rd","-mbcmp","rd","-precmp","rd","-cmp","rd",
                "-g", str(mpeg4_gop), "-bf","0","-sc_threshold","0",
                "-pix_fmt","yuv420p",
                "-r", fps_str, "-vsync","cfr",
            ]

        _run([
            "ffmpeg","-y","-i", combined,
            "-vf", f"select='{select_expr}',setpts=N/FRAME_RATE/TB",
            "-an",
        ] + enc + [output_path], verbose=verbose)
