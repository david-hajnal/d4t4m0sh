# mosh_algorithms/ui_keyframe_editor.py
import os, json, shutil, subprocess, tempfile, curses, time
import av
import cv2

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
    if abs(fps - 23.976) < 0.05: return "24000/1001"
    if abs(fps - 29.97)  < 0.05: return "30000/1001"
    if abs(fps - 59.94)  < 0.1:  return "60000/1001"
    return str(max(1, int(round(fps))))

def _codec_default():
    return "h264_videotoolbox" if os.uname().sysname == "Darwin" else "libx264"

def _collect_keyframes(path):
    """Return: rows = [(frame_idx, t_sec_or_None, pict_name, is_key)], key_idx = [i of rows where keyframe]."""
    rows, key_idx = [], []
    with av.open(path) as cont:
        v = next(s for s in cont.streams if s.type == "video")
        tb = v.time_base
        fps = float(v.average_rate) if v.average_rate else None
        for i, frame in enumerate(cont.decode(video=0)):
            pict = getattr(frame, "pict_type", None)
            pict_name = getattr(pict, "name", None) or str(pict).split(".")[-1]
            key = bool(getattr(frame, "key_frame", False))
            if frame.pts is not None and (getattr(frame, "time_base", None) or tb) is not None:
                t = float(frame.pts * (getattr(frame, "time_base", None) or tb))
            elif fps:
                t = float(i) / float(fps)
            else:
                t = None
            rows.append((i, t, pict_name, key))
            if key or pict_name in ("I","SI","IDR"):
                key_idx.append(len(rows)-1)  # index in rows list
    return rows, key_idx

def _preview_frame(path, frame_index):
    """Open a simple OpenCV window showing the requested frame index."""
    try:
        with av.open(path) as cont:
            for idx, frame in enumerate(cont.decode(video=0)):
                if idx == frame_index:
                    img = frame.to_ndarray(format="bgr24")
                    cv2.imshow("Preview (press any key)", img)
                    cv2.waitKey(0)
                    cv2.destroyAllWindows()
                    return
    except Exception:
        pass  # preview is best-effort

def _build_select_not_expr(drop_frame_numbers):
    """ffmpeg select keeps frames where expr is true → use not(eq(n, ...)+...)."""
    if not drop_frame_numbers:
        return "1"
    parts = [f"eq(n\\,{n})" for n in sorted(set(drop_frame_numbers))]
    return f"not({' + '.join(parts)})"

def _draw_timeline(stdscr, y, W, rows, marked_set, cursor_row):
    """
    Compact timeline: compress all frames into W-2 bins.
      'I'  = I-frame kept
      'X'  = I-frame marked for drop
      '.'  = no I in bin
    Cursor shown as '^' under the column.
    """
    if W < 20: return
    nframes = len(rows)
    bins = max(10, W - 2)
    col_for_row = lambda r: min(bins-1, int(r * bins / max(1, nframes)))
    # Precompute which bins contain I-frames (kept or marked)
    bin_char = ['.'] * bins
    for i, (fidx, _, pict, key) in enumerate(rows):
        if pict in ("I","SI","IDR") or key:
            c = 'X' if i in marked_set and i != 0 else 'I'
            col = col_for_row(i)
            bin_char[col] = c
    # Render line
    line = ''.join(bin_char)
    stdscr.addstr(y, 1, line[:W-2])
    # Cursor caret
    col = col_for_row(cursor_row)
    caret = [' '] * len(line)
    if 0 <= col < len(caret):
        caret[col] = '^'
    stdscr.addstr(y+1, 1, ''.join(caret)[:W-2])

def _tui(rows, key_rows, stdscr):
    curses.curs_set(0)
    stdscr.nodelay(False)
    H, W = stdscr.getmaxyx()
    help1 = "↑/↓ move  SPACE toggle  A drop-all-I(except first)  N keep-all  P preview  +/- postcut"
    help2 = "G next-I  g prev-I  T toggle timeline  D do-all+postcut(=3)  ENTER encode  Q quit"
    sel = 0
    timeline = True
    # Default: mark all I (except very first frame) to drop
    marked = set(i for i,(idx,t,p,ky) in enumerate(rows) if (idx != 0 and (p in ("I","SI","IDR") or ky)))
    postcut = 0

    def jump_next_i(cur, direction):
        """direction=+1 or -1; jump to nearest I-frame row index from key_rows."""
        if not key_rows: return cur
        # find current position in key_rows
        # first, location of nearest keyframe <= cur
        left = 0
        right = len(key_rows)-1
        pos = 0
        while left <= right:
            mid = (left+right)//2
            if key_rows[mid] <= cur:
                pos = mid; left = mid+1
            else:
                right = mid-1
        # move
        pos = max(0, min(len(key_rows)-1, pos + (1 if direction > 0 else -1)))
        return key_rows[pos]

    def redraw():
        stdscr.erase()
        stdscr.addstr(0, 0, help1[:W-1])
        stdscr.addstr(1, 0, help2[:W-1])
        stdscr.addstr(2, 0, f"Keyframes found: {len(key_rows)}   marked-to-drop: {sum(1 for i in key_rows if i in marked and rows[i][0]!=0)}   postcut={postcut}")
        rowstart = 3
        if timeline:
            _draw_timeline(stdscr, rowstart, W, rows, marked, sel)
            rowstart += 2
        stdscr.addstr(rowstart, 0, "  #   idx      time(s)   type  key  drop")
        max_rows = H - rowstart - 1
        start = max(0, min(sel - max_rows//2, len(rows) - max_rows))
        end = min(len(rows), start + max_rows)
        for i in range(start, end):
            idx, t, p, ky = rows[i]
            line = f"{i:3d}  {idx:6d}  {'' if t is None else f'{t:8.3f}'}   {p:>3}   {int(ky)}    {'X' if i in marked and i!=0 else ' '}"
            if i == sel:
                stdscr.attron(curses.A_REVERSE)
                stdscr.addstr(rowstart + 1 + i - start, 0, line[:W-1])
                stdscr.attroff(curses.A_REVERSE)
            else:
                stdscr.addstr(rowstart + 1 + i - start, 0, line[:W-1])
        stdscr.refresh()

    while True:
        redraw()
        ch = stdscr.getch()
        if ch in (ord('q'), ord('Q')):
            return None, None
        elif ch in (curses.KEY_UP, ord('k')):
            sel = max(0, sel - 1)
        elif ch in (curses.KEY_DOWN, ord('j')):
            sel = min(len(rows) - 1, sel + 1)
        elif ch in (ord('t'), ord('T')):
            timeline = not timeline
        elif ch in (ord('g'),):
            sel = jump_next_i(sel, -1)
        elif ch in (ord('G'),):
            sel = jump_next_i(sel, +1)
        elif ch in (ord(' '), ord('x'), ord('X')):
            if sel != 0:
                if sel in marked: marked.remove(sel)
                else: marked.add(sel)
        elif ch in (ord('a'), ord('A')):
            marked = set(i for i in key_rows if rows[i][0] != 0)
        elif ch in (ord('n'), ord('N')):
            marked = set()
        elif ch in (ord('+'), ord('=')):
            postcut = min(postcut + 1, 30)
        elif ch in (ord('-'), ord('_')):
            postcut = max(postcut - 1, 0)
        elif ch in (ord('d'), ord('D')):
            # One-shot: drop all boundary I's + set postcut=3 (you can tweak after)
            marked = set(i for i in key_rows if rows[i][0] != 0)
            postcut = max(postcut, 3)
        elif ch in (ord('p'), ord('P')):
            curses.def_prog_mode()
            curses.endwin()
            _preview_frame(_tui.video_path, rows[sel][0])
            time.sleep(0.05)
            stdscr.refresh()
        elif ch in (curses.KEY_ENTER, 10, 13):
            # Build frame numbers to drop: all selected keyframes + postcut frames after each selected boundary
            drop_frames = []
            for i in sorted(marked):
                start_frame = rows[i][0]
                if start_frame == 0:  # never drop very first frame
                    continue
                drop_frames.extend(start_frame + k for k in range(0, postcut + 1))
            return sorted(set(drop_frames)), postcut

def process(input_path: str, output_path: str, alpha=0.85, block=16, radius=8,
            gop=9999, codec=None, verbose=False):
    """
    Interactive keyframe editor with timeline and auto postcut:
      1) Normalize each input to same WxH/FPS, mosh-friendly intermediate (AVI/mpeg4).
      2) Concat into one combined AVI.
      3) TUI: toggle which I-frames to DROP; '+'/'-' sets how many frames to drop AFTER each dropped boundary.
         - 'D' quickly drops all boundaries and sets postcut=3 (adjustable).
         - 'G'/'g' jump next/prev I. 'T' toggles the timeline. 'P' previews the current frame.
      4) Final single encode with long GOP, no B-frames.
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

    base_w, base_h, base_fps = _ffprobe(inputs[0])
    base_w = (base_w // 2) * 2
    base_h = (base_h // 2) * 2
    fps_str = _safe_fps_str(base_fps)

    wants_mp4 = output_path.lower().endswith((".mp4", ".mov", ".m4v"))
    mpeg4_gop = min(int(gop), 600)  # mpeg4 cap

    with tempfile.TemporaryDirectory(prefix="mosh_ui_") as tmp:
        # 1) Normalize each clip
        norm_paths = []
        for idx, src in enumerate(inputs):
            dst = os.path.join(tmp, f"norm_{idx:03d}.avi")
            _run([
                "ffmpeg","-y","-i", src,
                "-an",
                "-vf", f"scale=trunc(iw/2)*2:trunc(ih/2)*2,scale={base_w}:{base_h},fps={fps_str}",
                "-r", fps_str, "-vsync", "cfr",
                "-c:v","mpeg4","-qscale:v","6",  # a bit blockier by default
                "-g", str(mpeg4_gop), "-bf","0","-sc_threshold","0",
                "-pix_fmt","yuv420p",
                dst
            ], verbose=verbose)
            norm_paths.append(dst)

        # 2) Concat
        n = len(norm_paths)
        concat_inputs = []
        for p in norm_paths: concat_inputs.extend(["-i", p])
        filtergraph = f"{''.join([f'[{i}:v]' for i in range(n)])}concat=n={n}:v=1:a=0"
        combined = os.path.join(tmp, "combined.avi")
        _run(["ffmpeg","-y"] + concat_inputs + [
            "-filter_complex", filtergraph,
            "-an",
            "-c:v","mpeg4","-qscale:v","6",
            "-g", str(mpeg4_gop), "-bf","0","-sc_threshold","0",
            "-pix_fmt","yuv420p",
            "-r", fps_str, "-vsync", "cfr",
            combined
        ], verbose=verbose)

        # 3) TUI
        rows, key_rows = _collect_keyframes(combined)
        if not rows:
            raise RuntimeError("No frames decoded from combined video.")
        _tui.video_path = combined
        def _run_tui(stdscr): return _tui(rows, key_rows, stdscr)
        drop_frames, postcut = curses.wrapper(_run_tui)
        if drop_frames is None:
            raise RuntimeError("Cancelled by user.")

        # 4) Final encode (single pass): select not(drop_frames) + setpts
        select_expr = _build_select_not_expr(drop_frames)
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
                "-c:v","mpeg4","-qscale:v","6",
                "-g", str(mpeg4_gop), "-bf","0","-sc_threshold","0",
                "-pix_fmt","yuv420p",
                "-r", fps_str, "-vsync","cfr",
            ]

        _run([
            "ffmpeg","-y","-i", combined,
            "-vf", f"select='{select_expr}',setpts=N/FRAME_RATE/TB",
            "-an",
        ] + enc + [output_path], verbose=verbose)
