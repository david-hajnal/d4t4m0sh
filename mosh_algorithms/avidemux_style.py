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

import os, json, shutil, subprocess, tempfile
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
  """Return (codec_name, width, height, pix_fmt, r_frame_rate, fourcc) for the first video stream."""
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

def _all_same(lst):
  return all(x == lst[0] for x in lst)

def _validate_inputs_for_copy(inputs, verbose=False):
  """Ensure all inputs are AVI + MPEG-4 Part 2 (XVID/FMP4), same core params."""
  codecs, sizes, pix, rates, tags = [], [], [], [], []
  for p in inputs:
    if not p.lower().endswith(".avi"):
      raise RuntimeError(f"Input is not .avi: {p} (convert to Xvid/AVI first)")
    c, w, h, pf, rf, tag = _ffprobe_stream(p)
    codecs.append(c)
    sizes.append((w,h))
    pix.append(pf)
    rates.append(rf)
    tags.append(tag)
    if verbose:
      print(f"[CHK] {os.path.basename(p)} codec={c} tag={tag} {w}x{h} {pf} {rf}")
    if c not in ("mpeg4", "msmpeg4v3", "mpeg4video"):  # ffprobe names vary; mpeg4 is the target
      raise RuntimeError(f"{p}: codec {c} not MPEG-4 ASP (Xvid/DivX). Use convert_to_xvid.sh first.")
    if pf != "yuv420p":
      raise RuntimeError(f"{p}: pix_fmt {pf} != yuv420p")
    if tag not in ("XVID","FMP4","DIVX","MP43","MP42"):
      # XVID/FMP4 typical; DIVX also seen. MP4V in mp4 container would not be AVI.
      pass
  if not (_all_same(codecs) and _all_same(sizes) and _all_same(pix) and _all_same(rates)):
    raise RuntimeError("Inputs differ in codec/size/fps/pix_fmt; re-convert them uniformly (use convert_to_xvid.sh).")

def _build_concat_listfile(inputs, list_path):
  with open(list_path, "w", encoding="utf-8") as f:
    for p in inputs:
      f.write("file '" + p.replace("'", "'\\''") + "'\n")

def _concat_copy(inputs, out_path, verbose=False):
  """ffmpeg concat demuxer, -c copy, video-only AVI."""
  listfile = os.path.join(os.path.dirname(out_path), "concat.txt")
  _build_concat_listfile(inputs, listfile)
  _run([
    "ffmpeg","-y",
    "-f","concat","-safe","0","-i", listfile,
    "-map","0:v:0","-c","copy","-an",
    out_path
  ], verbose=verbose)
  return out_path

def process(input_path: str, output_path: str, alpha=0.85, block=16, radius=8,
    gop=9999, codec=None, verbose=False, postcut=6, drop_mode="all_after_first",
    pframe_dup_start=None, **_):
  """
  Avidemux-style (no re-encode) datamosh.

  Parameters:
    input_path        : comma-separated list of Xvid/AVI files (same WxH/FPS/YUV420p, B=0).
    output_path       : MUST end with .avi (remuxed AVI).
    postcut           : int >= 0. After removing an I-frame, also drop the next N packets.
    drop_mode         : 'all_after_first' (default) drops every keyframe after the first one,
                        or 'boundaries_only' (experimental) tries to drop the first keyframe per segment only.
    pframe_dup_start  : float or None. If specified, P-frame duplication (via postcut) only starts after
                        this timestamp (in seconds). Before this time, all P-frames are preserved.

  Notes:
    - Output is VIDEO-ONLY AVI (no audio) to avoid desync.
    - Playback: use VLC/MPV. Some apps dislike broken GOPs (expected for datamosh).
  """
  if not output_path.lower().endswith(".avi"):
    raise RuntimeError("avidemux_style writes AVI only. Use .avi as output.")
  inputs = [s.strip() for s in (input_path or "").split(",") if s.strip()]
  if not inputs:
    raise RuntimeError("Provide at least one input AVI (comma-separated).")
  for p in inputs:
    if not os.path.exists(p):
      raise FileNotFoundError(p)

  # Validate inputs are remux-compatible
  _validate_inputs_for_copy(inputs, verbose=verbose)

  with tempfile.TemporaryDirectory(prefix="avidemux_style_") as tmp:
    # 1) Concat WITHOUT re-encode
    combined = os.path.join(tmp, "combined.avi")
    _concat_copy(inputs, combined, verbose=verbose)

    # 2) Packet surgery: drop I-packets (and postcut packets) without re-encode
    in_ct = av.open(combined)
    vin = next(s for s in in_ct.streams if s.type == "video")

    # Prepare output container & stream (copy parameters, but be compatible with older PyAV)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    out_ct = av.open(output_path, mode="w")

    # Some PyAV versions support template=..., others require a codec name.
    try:
      vout = out_ct.add_stream(template=vin)  # newer PyAV
    except TypeError:
      # Fallback for older PyAV
      codec_name = getattr(getattr(vin, "codec_context", None), "name", None) or \
                   getattr(getattr(vin, "codec", None), "name", None) or "mpeg4"
      vout = out_ct.add_stream(codec_name)
      # Align time base so we can copy packets 1:1
      try: vout.time_base = vin.time_base
      except Exception: pass
      # Preserve extradata if available (VOL headers etc.)
      try: vout.codec_context.extradata = vin.codec_context.extradata
      except Exception: pass

    # Preserve codec tag (XVID/FMP4) helps some players
    try: vout.codec_tag = vin.codec_tag
    except Exception: pass

    keep_first_i = True
    pending_drop = 0
    pc = max(0, int(postcut))

    # Convert pframe_dup_start to time_base units if specified
    pframe_dup_start_pts = None
    if pframe_dup_start is not None:
      pframe_dup_start_pts = int(pframe_dup_start / float(vin.time_base))

    # Demux & remux packets (video-only). Drop I-packets after the first, plus 'postcut' packets after each.
    for pkt in in_ct.demux(vin):
      if pkt.is_keyframe:
        if keep_first_i:
          keep_first_i = False
          # rescale timestamps if time_bases differ
          try:
            pkt.rescale_ts(vin.time_base, vout.time_base)
          except Exception:
            pass
          pkt.stream = vout
          out_ct.mux(pkt)
        else:
          # drop this I and arm postcut
          pending_drop = pc
      else:
        # Check if we should skip postcut dropping based on timestamp
        if pending_drop > 0:
          # If pframe_dup_start is set and we haven't reached that time yet, don't drop
          if pframe_dup_start_pts is not None and pkt.pts < pframe_dup_start_pts:
            # Before the start time: keep this P-frame regardless of pending_drop
            pass
          else:
            # After the start time (or no start time set): apply postcut dropping
            pending_drop -= 1
            continue
        # keep this P-frame (or whatever) — rescale ts as needed
        try:
          pkt.rescale_ts(vin.time_base, vout.time_base)
        except Exception:
          pass
        pkt.stream = vout
        out_ct.mux(pkt)

    out_ct.close()
    in_ct.close()


  if verbose:
      print(f"[OK] Wrote {output_path} (video-only AVI). If a player refuses, try VLC/MPV.")
