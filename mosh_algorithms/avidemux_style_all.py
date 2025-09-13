# mosh_algorithms/avidemux_style_all.py
import os, json, shutil, subprocess, tempfile, random
import av

def _run(cmd, verbose=False):
    loglevel = "info" if verbose else "error"
    if cmd and cmd[0] == "ffmpeg" and "-loglevel" not in cmd:
        cmd = cmd[:1] + ["-hide_banner", "-loglevel", loglevel] + cmd[1:]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg exit {p.returncode}\nCMD: {' '.join(cmd)}\n{p.stderr}")
    if verbose and p.stderr: print(p.stderr)
    return p

def _ffprobe(path):
    p = subprocess.run(
        ["ffprobe","-v","error","-select_streams","v:0",
         "-show_entries","stream=width,height,avg_frame_rate","-of","json", path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    if p.returncode != 0: raise RuntimeError(f"ffprobe failed for {path}:\n{p.stderr}")
    s = json.loads(p.stdout)["streams"][0]
    w, h = int(s["width"]), int(s["height"])
    fr = s.get("avg_frame_rate","0/0")
    try:
        n,d = fr.split("/"); fps = float(n)/float(d) if d!="0" else 30.0
    except Exception: fps = 30.0
    return w, h, fps

def _safe_fps_str(fps):
    if abs(fps-23.976)<0.05: return "24000/1001"
    if abs(fps-29.97)<0.05:  return "30000/1001"
    if abs(fps-59.94)<0.10:  return "60000/1001"
    return str(max(1,int(round(fps))))

def _have_libxvid():
    try:
        p = subprocess.run(["ffmpeg","-hide_banner","-v","error","-h","encoder=libxvid"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return p.returncode == 0
    except Exception:
        return False

def _to_xvid(src, dst, w, h, fps_str, q, gop, hold_sec, is_last, verbose=False):
    # Build VF chain: even -> scale -> fps (+ optional tpad hold on non-last clips)
    vf = f"scale=trunc(iw/2)*2:trunc(ih/2)*2,scale={w}:{h},fps={fps_str}"
    if hold_sec and hold_sec > 0 and not is_last:
        vf += f",tpad=stop_mode=clone:stop_duration={float(hold_sec)}"
    vcodec = ["-c:v","libxvid"] if _have_libxvid() else ["-c:v","mpeg4","-vtag","XVID"]
    _run([
        "ffmpeg","-y","-i", src,
        "-an","-vf", vf, "-r", fps_str,
        *vcodec, "-qscale:v", str(int(q)),
        "-g", str(min(int(gop),600)), "-bf","0","-sc_threshold","0",
        "-pix_fmt","yuv420p", dst
    ], verbose=verbose)

def _concat_copy(avilist, out_path, verbose=False):
    listfile = os.path.join(os.path.dirname(out_path), "concat.txt")
    with open(listfile,"w",encoding="utf-8") as f:
        for p in avilist: f.write("file '" + p.replace("'", "'\\''") + "'\n")
    _run(["ffmpeg","-y","-f","concat","-safe","0","-i", listfile,
          "-map","0:v:0","-c","copy","-an", out_path], verbose=verbose)

def _packet_surgery(in_avi, out_avi, postcut, postcut_rand, drop_mode, verbose=False):
    pc_range = None
    if postcut_rand:
        a,b = postcut_rand
        pc_range = (int(a), int(b))
    in_ct = av.open(in_avi)
    vin = next(s for s in in_ct.streams if s.type=="video")
    os.makedirs(os.path.dirname(out_avi) or ".", exist_ok=True)
    out_ct = av.open(out_avi, mode="w")
    try:
        vout = out_ct.add_stream(template=vin)
    except TypeError:
        codec_name = getattr(getattr(vin,"codec_context",None),"name",None) or "mpeg4"
        vout = out_ct.add_stream(codec_name)
        try: vout.time_base = vin.time_base
        except Exception: pass
        try: vout.codec_context.extradata = vin.codec_context.extradata
        except Exception: pass
    try: vout.codec_tag = vin.codec_tag
    except Exception: pass

    keep_first = True
    pending = 0
    boundaries_left = None
    if drop_mode == "boundaries_only":
        # Roughly: drop only first I encountered after each join.
        # We approximate joins by counting I's: assume each clip starts with an I.
        # In practice with Xvid this is true; if not, effect still works once P's arrive.
        boundaries_left = 10**9  # we’ll cap by observed I’s after the first frame
    for pkt in in_ct.demux(vin):
        if pkt.is_keyframe:
            if keep_first:
                keep_first = False
                try: pkt.rescale_ts(vin.time_base, vout.time_base)
                except Exception: pass
                pkt.stream = vout; out_ct.mux(pkt)
            else:
                if drop_mode == "boundaries_only":
                    if boundaries_left > 0:
                        boundaries_left -= 1
                        n = (random.randint(*pc_range) if pc_range else int(postcut))
                        pending = max(0,n)
                    else:
                        try: pkt.rescale_ts(vin.time_base, vout.time_base)
                        except Exception: pass
                        pkt.stream = vout; out_ct.mux(pkt)
                else:
                    n = (random.randint(*pc_range) if pc_range else int(postcut))
                    pending = max(0,n)
        else:
            if pending > 0:
                pending -= 1
            else:
                try: pkt.rescale_ts(vin.time_base, vout.time_base)
                except Exception: pass
                pkt.stream = vout; out_ct.mux(pkt)
    out_ct.close(); in_ct.close()
    if verbose: print(f"[OK] Surgery wrote {out_avi}")

def process(input_path: str, output_path: str, alpha=0.85, block=16, radius=8,
            gop=600, codec=None, verbose=False,
            mosh_q=10, postcut=10, postcut_rand=None, drop_mode="all_after_first",
            hold_sec=0.0, audio_from=None, **_):
    """
    One-shot Avidemux-style pipeline:
      - Convert each input to Xvid/AVI (even WxH, CFR, yuv420p, B=0, long GOP).
      - Optionally add a 'smear hold' (tpad clone) at end of each clip (except last).
      - Concat with copy (no re-encode).
      - Packet surgery: drop all I after the first (+ postcut / random postcut).
      - Deliver:
          * If output is .avi  -> write moshed AVI (video-only).
          * If output is .mp4 -> transcode moshed AVI to MP4 (x264) and optionally mux audio from --audio_from.
    """
    inputs = [s.strip() for s in (input_path or "").split(",") if s.strip()]
    if not inputs: raise RuntimeError("Provide at least one input file (comma-separated).")
    for p in inputs:
        if not os.path.exists(p): raise FileNotFoundError(p)

    # Baseline from first input
    w,h,fps = _ffprobe(inputs[0])
    fps_str = _safe_fps_str(fps)

    with tempfile.TemporaryDirectory(prefix="avidemux_all_") as tmp:
        # 1) Convert -> Xvid AVIs (with optional holds)
        avis=[]
        for i,src in enumerate(inputs):
            dst = os.path.join(tmp, f"xvid_{i:03d}.avi")
            _to_xvid(src, dst, (w//2)*2, (h//2)*2, fps_str, mosh_q, gop, hold_sec, is_last=(i==len(inputs)-1), verbose=verbose)
            avis.append(dst)

        # 2) Concat copy
        combined = os.path.join(tmp, "combined.avi")
        _concat_copy(avis, combined, verbose=verbose)

        # 3) Packet surgery
        moshed_avi = os.path.join(tmp, "moshed.avi")
        if postcut_rand:
            try:
                a,b = postcut_rand.split(":"); postcut_rand = (int(a),int(b))
            except Exception:
                raise ValueError(f"Invalid --postcut_rand '{postcut_rand}', expected A:B")
        _packet_surgery(combined, moshed_avi, postcut, postcut_rand, drop_mode, verbose=verbose)

        # 4) Deliver
        if output_path.lower().endswith(".avi"):
            shutil.move(moshed_avi, output_path)
            if verbose: print(f"[OK] AVI written: {output_path}")
        else:
            # Transcode to MP4 (software x264) and optional audio
            cmd = ["ffmpeg","-y","-i", moshed_avi]
            if audio_from and os.path.exists(audio_from):
                cmd += ["-i", audio_from, "-map","0:v:0","-map","1:a:0","-shortest"]
            else:
                cmd += ["-map","0:v:0","-an"]
            cmd += ["-c:v","libx264","-crf","20","-preset","medium","-pix_fmt","yuv420p","-movflags","+faststart", output_path]
            _run(cmd, verbose=verbose)
            if verbose: print(f"[OK] MP4 written: {output_path}")
