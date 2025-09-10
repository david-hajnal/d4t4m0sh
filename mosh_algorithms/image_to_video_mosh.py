# mosh_algorithms/image_to_video_mosh.py
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
    cmd = ["ffprobe","-v","error","-select_streams","v:0",
           "-show_entries","stream=width,height,avg_frame_rate","-of","json", path]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}:\n{p.stderr}")
    s = json.loads(p.stdout)["streams"][0]
    w, h = int(s["width"]), int(s["height"])
    fr = s.get("avg_frame_rate","0/0")
    try:
        n,d = fr.split("/")
        fps = float(n)/float(d) if d!="0" else 30.0
    except Exception:
        fps = 30.0
    return w, h, fps

def _safe_fps_str(fps):
    if abs(fps-23.976)<0.05: return "24000/1001"
    if abs(fps-29.97) <0.05: return "30000/1001"
    if abs(fps-59.94)<0.10:  return "60000/1001"
    return str(max(1, int(round(fps))))

def _collect_iframes(path):
    idxs = []
    with av.open(path) as cont:
        v = next(s for s in cont.streams if s.type=="video")
        for i, frame in enumerate(cont.decode(video=0)):
            pict = getattr(frame, "pict_type", None)
            name = getattr(pict, "name", None) or str(pict).split(".")[-1]
            if name in ("I","SI","IDR") or bool(getattr(frame,"key_frame",False)):
                idxs.append(i)
    return idxs

def _build_select_not(drop_list):
    if not drop_list: return "1"
    parts = [f"eq(n\\,{n})" for n in sorted(set(drop_list))]
    return f"not({' + '.join(parts)})"

def _codec_default():
    return "h264_videotoolbox" if os.uname().sysname == "Darwin" else "libx264"

def _make_image_motion_clip(img_path, out_path, w, h, fps_str, dur, mpeg4_gop, kb_mode, verbose=False):
    # Motion seeds vectors so the smear takes hold after the boundary.
    src = ["-loop","1","-framerate", fps_str, "-t", str(dur), "-i", img_path]
    if kb_mode == "zoom_in":
        # gentle zoom using zoompan
        zoom_step = 0.0015
        vf = (
            f"scale={w}:{h},"
            f"zoompan=z='min(max(pzoom\\,1.0)+{zoom_step}\\,1.15)':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={w}x{h},"
            f"fps={fps_str}"
        )
    else:
        # tiny rotation jitter (robust)
        vf = (
            f"scale={w}:{h},"
            f"rotate='0.02*sin(2*PI*t/{max(dur,0.1)})':ow=iw:oh=ih:c=black:fillcolor=black,"
            f"fps={fps_str}"
        )
    _run([
        "ffmpeg","-y", *src,
        "-an",
        "-vf", vf,
        "-r", fps_str, "-vsync","cfr",
        "-c:v","mpeg4","-qscale:v","6",
        "-g", str(mpeg4_gop), "-bf","0","-sc_threshold","0",
        "-pix_fmt","yuv420p",
        out_path
    ], verbose=verbose)

def process(input_path: str, output_path: str, alpha=0.85, block=16, radius=8,
            gop=9999, codec=None, verbose=False,
            image=None, img_dur=3.0, kb_mode="rotate", postcut=4):
    """
    Image -> Video datamosh preset:
      - Build a motion clip from the still (first segment).
      - Normalize the video to same WxH/FPS.
      - Concat [image_clip, video] as one stream.
      - Drop ALL I-frames except the very first (frame 0), plus N 'postcut' frames after each drop.
      - Final single encode: long GOP, no B-frames.

    Params:
      image: path to still (required)
      img_dur: seconds of the image motion clip (2–5s works well)
      kb_mode: 'rotate' (default) or 'zoom_in'
      postcut: frames to drop after each removed I (3–6 recommended)
    """
    if not image or not os.path.exists(image):
        raise RuntimeError("Provide --image path to a still file that exists.")
    codec = codec or _codec_default()
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise RuntimeError("ffmpeg/ffprobe not found. Install via Homebrew: brew install ffmpeg")

    # Normalize baseline from the video
    base_w, base_h, base_fps = _ffprobe(input_path)
    base_w = (base_w // 2) * 2
    base_h = (base_h // 2) * 2
    fps_str = _safe_fps_str(base_fps)
    mpeg4_gop = min(int(gop), 600)
    wants_mp4 = output_path.lower().endswith((".mp4",".mov",".m4v"))

    with tempfile.TemporaryDirectory(prefix="img2vid_mosh_") as tmp:
        # 1) Image motion clip (first segment)
        img_clip = os.path.join(tmp, "image_clip.avi")
        _make_image_motion_clip(image, img_clip, base_w, base_h, fps_str, img_dur, mpeg4_gop, kb_mode, verbose=verbose)

        # 2) Normalize video to AVI/MPEG-4
        norm_vid = os.path.join(tmp, "video_norm.avi")
        _run([
            "ffmpeg","-y","-i", input_path,
            "-an",
            "-vf", f"scale=trunc(iw/2)*2:trunc(ih/2)*2,scale={base_w}:{base_h},fps={fps_str}",
            "-r", fps_str, "-vsync","cfr",
            "-c:v","mpeg4","-qscale:v","6",
            "-g", str(mpeg4_gop), "-bf","0","-sc_threshold","0",
            "-pix_fmt","yuv420p",
            norm_vid
        ], verbose=verbose)

        # 3) Concat [image_clip, video]
        combined = os.path.join(tmp, "combined.avi")
        _run([
            "ffmpeg","-y","-i", img_clip,"-i", norm_vid,
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0",
            "-an",
            "-c:v","mpeg4","-qscale:v","6",
            "-g", str(mpeg4_gop), "-bf","0","-sc_threshold","0",
            "-pix_fmt","yuv420p",
            "-r", fps_str, "-vsync","cfr",
            combined
        ], verbose=verbose)

        # 4) Drop all I except very first (0) + postcut frames after each
        i_frames = _collect_iframes(combined)
        drop = set()
        for i in i_frames:
            if i == 0: 
                continue
            for k in range(0, max(0,int(postcut)) + 1):
                drop.add(i + k)
        select_expr = _build_select_not(sorted(drop))

        # 5) Final encode
        if wants_mp4:
            enc = ["-c:v", codec, "-g", str(gop), "-bf","0","-sc_threshold","0",
                   "-pix_fmt","yuv420p", "-movflags","+faststart",
                   "-r", fps_str, "-vsync","cfr"]
            if codec == "libx264":
                enc += ["-x264-params","keyint=9999:min-keyint=9999:scenecut=0:bframes=0:ref=1:weightp=0"]
        else:
            enc = ["-c:v","mpeg4","-qscale:v","6", "-g", str(mpeg4_gop),
                   "-bf","0","-sc_threshold","0", "-pix_fmt","yuv420p",
                   "-r", fps_str, "-vsync","cfr"]

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        _run([
            "ffmpeg","-y","-i", combined,
            "-vf", f"select='{select_expr}',setpts=N/FRAME_RATE/TB",
            "-an",
        ] + enc + [output_path], verbose=verbose)
