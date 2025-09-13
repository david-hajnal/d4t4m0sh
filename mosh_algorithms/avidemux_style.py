# mosh_algorithms/avidemux_style.py
#
# Avidemux 2.5.x style datamosh:
# - expects pre-normalized Xvid/MPEG-4 ASP in AVI (same WxH/FPS/YUV420p, B=0)
# - concatenates via ffmpeg concat demuxer (-c copy, no re-encode)
# - removes all keyframes (I) except the very first at PACKET level (no re-encode)
# - optionally drops N packets immediately after each removed I (postcut) to intensify smear
# - writes video-only AVI (no audio) for simplicity & to avoid A/V desync
#
# Play in VLC or similar tolerant players.


# mosh_algorithms/avidemux_style.py
import os, json, shutil, subprocess, tempfile, random
import av  # pip install av

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

def _ffprobe_stream(path):
    p = subprocess.run(
        ["ffprobe","-v","error","-select_streams","v:0","-show_entries",
         "stream=codec_name,width,height,pix_fmt,r_frame_rate,codec_tag_string","-of","json", path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}:\n{p.stderr}")
    s = json.loads(p.stdout)["streams"][0]
    return (
        s.get("codec_name",""),
        int(s.get("width",0) or 0),
        int(s.get("height",0) or 0),
        s.get("pix_fmt",""),
        s.get("r_frame_rate",""),
        (s.get("codec_tag_string","") or "").upper()
    )

def _all_same(lst): return all(x == lst[0] for x in lst)

def _validate_inputs_for_copy(inputs, verbose=False):
    codecs, sizes, pix, rates, tags = [], [], [], [], []
    for p in inputs:
        if not p.lower().endswith(".avi"):
            raise RuntimeError(f"Input is not .avi: {p} (convert to Xvid/AVI first)")
        c, w, h, pf, rf, tag = _ffprobe_stream(p)
        codecs.append(c); sizes.append((w,h)); pix.append(pf); rates.append(rf); tags.append(tag)
        if verbose:
            print(f"[CHK] {os.path.basename(p)} codec={c} tag={tag} {w}x{h} {pf} {rf}")
        if c not in ("mpeg4","msmpeg4v3","mpeg4video"):
            raise RuntimeError(f"{p}: codec {c} not MPEG-4 ASP (Xvid/DivX). Use convert_to_xvid.sh first.")
        if pf != "yuv420p":
            raise RuntimeError(f"{p}: pix_fmt {pf} != yuv420p")
    if not (_all_same(codecs) and _all_same(sizes) and _all_same(pix) and _all_same(rates)):
        raise RuntimeError("Inputs differ in codec/size/fps/pix_fmt; re-convert them uniformly (use convert_to_xvid.sh).")

def _build_concat_listfile(inputs, list_path):
  with open(list_path, "w", encoding="utf-8") as f:
    for p in inputs:
      f.write("file '" + p.replace("'", "'\\''") + "'\n")

def _concat_copy(inputs, out_path, verbose=False):
    listfile = os.path.join(os.path.dirname(out_path), "concat.txt")
    _build_concat_listfile(inputs, listfile)
    _run([
        "ffmpeg","-y","-f","concat","-safe","0","-i", listfile,
        "-map","0:v:0","-c","copy","-an", out_path
    ], verbose=verbose)
    return out_path

def _parse_postcut_rand(s):
    if not s: return None
    try:
        a,b = s.split(":")
        a = int(a.strip()); b = int(b.strip())
        if a < 0 or b < a: raise ValueError
        return (a,b)
    except Exception:
        raise ValueError(f"Invalid --postcut-rand '{s}', expected A:B with non-negative ints and B>=A")

def process(input_path: str, output_path: str, alpha=0.85, block=16, radius=8,
            gop=9999, codec=None, verbose=False,
            postcut=8, postcut_rand=None, drop_mode="all_after_first", **_):
    """
    Avidemux-style (no re-encode) datamosh.

    Args:
      input_path  : comma-separated list of Xvid/AVI files (same WxH/FPS/YUV420p).
      output_path : .avi file (video-only).
      postcut     : int >= 0, drop N packets after each removed I.
      postcut_rand: "A:B" random range per boundary (overrides postcut when set).
      drop_mode   : 'all_after_first' (default) or 'boundaries_only' (drop only first I after each join).
    """
    if not output_path.lower().endswith(".avi"):
        raise RuntimeError("avidemux_style writes AVI only. Use .avi as output.")
    inputs = [s.strip() for s in (input_path or "").split(",") if s.strip()]
    if not inputs: raise RuntimeError("Provide at least one input AVI (comma-separated).")
    for p in inputs:
        if not os.path.exists(p): raise FileNotFoundError(p)

    _validate_inputs_for_copy(inputs, verbose=verbose)
    pc_range = _parse_postcut_rand(postcut_rand)

    with tempfile.TemporaryDirectory(prefix="avidemux_style_") as tmp:
        combined = os.path.join(tmp, "combined.avi")
        _concat_copy(inputs, combined, verbose=verbose)

        in_ct = av.open(combined)
        vin = next(s for s in in_ct.streams if s.type == "video")

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        out_ct = av.open(output_path, mode="w")
        # Stream creation – handle old/new PyAV
        try:
            vout = out_ct.add_stream(template=vin)
        except TypeError:
            codec_name = getattr(getattr(vin, "codec_context", None), "name", None) or \
                         getattr(getattr(vin, "codec", None), "name", None) or "mpeg4"
            vout = out_ct.add_stream(codec_name)
            try: vout.time_base = vin.time_base
            except Exception: pass
            try: vout.codec_context.extradata = vin.codec_context.extradata
            except Exception: pass
        try: vout.codec_tag = vin.codec_tag
        except Exception: pass

        keep_first_i = True
        pending_drop = 0
        boundaries_remaining = (len(inputs) - 1) if drop_mode == "boundaries_only" else None

        for pkt in in_ct.demux(vin):
            if pkt.is_keyframe:
                if keep_first_i:
                    keep_first_i = False
                    try: pkt.rescale_ts(vin.time_base, vout.time_base)
                    except Exception: pass
                    pkt.stream = vout; out_ct.mux(pkt)
                else:
                    # Drop this I-frame
                    if boundaries_remaining is None or boundaries_remaining > 0:
                        if boundaries_remaining is not None:
                            boundaries_remaining -= 1
                        # Set postcut for this boundary
                        n = (random.randint(*pc_range) if pc_range else int(postcut))
                        pending_drop = max(0, n)
                    else:
                        # Not at a boundary (and boundaries_only): keep interior I
                        try: pkt.rescale_ts(vin.time_base, vout.time_base)
                        except Exception: pass
                        pkt.stream = vout; out_ct.mux(pkt)
            else:
                if pending_drop > 0:
                    pending_drop -= 1
                else:
                    try: pkt.rescale_ts(vin.time_base, vout.time_base)
                    except Exception: pass
                    pkt.stream = vout; out_ct.mux(pkt)

        out_ct.close(); in_ct.close()
        if verbose:
            print(f"[OK] Wrote {output_path} (video-only AVI). Play with VLC/MPV; index-sensitive apps may complain.")
