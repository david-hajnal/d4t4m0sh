import os, json, shutil, subprocess, tempfile, math

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

def _ffprobe(first_path):
    cmd = [
        "ffprobe","-v","error","-select_streams","v:0",
        "-show_entries","stream=width,height,avg_frame_rate",
        "-of","json", first_path
    ]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {first_path}:\n{p.stderr}")
    s = json.loads(p.stdout)["streams"][0]
    w, h = int(s["width"]), int(s["height"])
    fr = s.get("avg_frame_rate", "0/0")
    try:
        n, d = fr.split("/")
        fps = float(n) / float(d) if d != "0" else 30.0
    except Exception:
        fps = 30.0
    return w, h, fps

def _safe_fps_str(fps):
    """Map to a safe CFR value with small den (<=65535)."""
    # Snap common rates
    if abs(fps - 23.976) < 0.05: return "24000/1001"
    if abs(fps - 29.97)  < 0.05: return "30000/1001"
    if abs(fps - 59.94)  < 0.1:  return "60000/1001"
    # Otherwise rounded integer
    return str(max(1, int(round(fps))))

def _codec_default():
    return "h264_videotoolbox" if os.uname().sysname == "Darwin" else "libx264"

def process(input_path: str, output_path: str, alpha=0.85, block=16, radius=8,
            gop=9999, codec=None, verbose=False):
    codec = codec or _codec_default()
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise RuntimeError("ffmpeg/ffprobe not found. brew install ffmpeg")

    inputs = [s.strip() for s in input_path.split(",") if s.strip()]
    for f in inputs:
        if not os.path.exists(f):
            raise FileNotFoundError(f)

    wants_mp4 = output_path.lower().endswith((".mp4", ".mov", ".m4v"))

    # Normalize to first clip's size/fps; also force EVEN dims
    base_w, base_h, base_fps = _ffprobe(inputs[0])
    base_w = (base_w // 2) * 2
    base_h = (base_h // 2) * 2
    fps_str = _safe_fps_str(base_fps)

    # MPEG-4 GOP cap
    mpeg4_gop = min(int(gop), 600)

    with tempfile.TemporaryDirectory(prefix="datamosh_multi_v2_") as tmp:
        norm_paths = []
        for idx, src in enumerate(inputs):
            dst = os.path.join(tmp, f"norm_{idx:03d}.avi")
            # Stage 1: normalize EACH clip (even dims + CFR) with safe MPEG-4 settings
            _run([
                "ffmpeg","-y","-i", src,
                "-an",
                "-vf", f"scale=trunc(iw/2)*2:trunc(ih/2)*2,scale={base_w}:{base_h},fps={fps_str}",
                "-r", fps_str, "-vsync", "cfr",
                "-c:v","mpeg4",
                "-qscale:v","6",
                "-g", str(mpeg4_gop), "-bf","0","-sc_threshold","0",
                "-pix_fmt","yuv420p",
                "-force_key_frames", "expr:gte(t,0)",
                dst
            ], verbose=verbose)
            norm_paths.append(dst)

        # Concat all normalized clips
        n = len(norm_paths)
        concat_inputs = []
        for p in norm_paths:
            concat_inputs.extend(["-i", p])

        # concat -> drop all I except frame 0 -> setpts
        filtergraph = (
            f"{''.join([f'[{i}:v]' for i in range(n)])}"
            f"concat=n={n}:v=1:a=0,"
            "select='eq(n\\,0)+not(eq(pict_type\\,I))',"
            "setpts=N/FRAME_RATE/TB"
        )

        # Single final encode (CFR + even dims preserved)
        if wants_mp4:
            enc = [
                "-c:v", codec,
                "-g", str(gop), "-bf", "0", "-sc_threshold", "0",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                "-r", fps_str, "-vsync", "cfr",
            ]
        else:
            enc = [
                "-c:v","mpeg4","-qscale:v","6",
                "-g", str(mpeg4_gop), "-bf", "0", "-sc_threshold", "0",
                "-pix_fmt","yuv420p",
                "-r", fps_str, "-vsync", "cfr",
            ]

        cmd = ["ffmpeg","-y"] + concat_inputs + [
            "-filter_complex", filtergraph,
            "-an",
        ] + enc + [output_path]

        _run(cmd, verbose=verbose)
