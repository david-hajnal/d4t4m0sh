#!/usr/bin/env python3
import argparse
import os
import re
import sys
import inspect

# make sure local package wins
sys.path.insert(0, os.path.dirname(__file__))

from mosh_algorithms import ALGORITHMS

VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm")

def default_output_path(in_path: str, algo: str) -> str:
    root, ext = os.path.splitext(in_path)
    if not ext:
        ext = ".mp4"
    return f"{root}.{algo}.mosh{ext}"

def scan_videos(dirpath: str):
    if not os.path.isdir(dirpath):
        return []
    out = []
    for name in sorted(os.listdir(dirpath)):
        p = os.path.join(dirpath, name)
        if os.path.isfile(p) and name.lower().endswith(VIDEO_EXTS):
            out.append(p)
    return out

def prompt_pick_order(files, multi=True):
    """
    Simple interactive selector:
      - Lists files with indices.
      - For multi=True: "Enter indices in desired order, comma-separated" (e.g. 3,1,2)
        Press ENTER for all (current order).
      - For multi=False: "Choose one index" (ENTER defaults to 1).
    Works even without extra dependencies.
    """
    print("\nFound videos:")
    for i, p in enumerate(files, 1):
        print(f"  [{i}] {os.path.basename(p)}")
    if multi:
        if sys.stdin.isatty():
            sel = input("\nEnter indices in desired order (e.g. 3,1,2), or press ENTER for all: ").strip()
        else:
            sel = ""
        if not sel:
            return files[:]
        indices = []
        for tok in sel.split(","):
            tok = tok.strip()
            if not tok:
                continue
            if not re.fullmatch(r"\d+", tok):
                print(f"  [WARN] Skipping invalid token: {tok}")
                continue
            i = int(tok)
            if 1 <= i <= len(files):
                if i not in indices:
                    indices.append(i)
            else:
                print(f"  [WARN] Index out of range: {i}")
        if not indices:
            print("  [INFO] No valid indices provided; using all files.")
            return files[:]
        ordered = [files[i - 1] for i in indices]
        print("\nOrder chosen:")
        for k, p in enumerate(ordered, 1):
            print(f"  {k}. {os.path.basename(p)}")
        return ordered
    else:
        if sys.stdin.isatty():
            sel = input("\nChoose ONE index (ENTER defaults to 1): ").strip()
        else:
            sel = "1"
        idx = 1
        if sel:
            if re.fullmatch(r"\d+", sel):
                idx = int(sel)
            else:
                print(f"  [WARN] Invalid input '{sel}', defaulting to 1.")
        if idx < 1 or idx > len(files):
            print(f"  [WARN] Index {idx} out of range, defaulting to 1.")
            idx = 1
        choice = files[idx - 1]
        print(f"Selected: {os.path.basename(choice)}")
        return [choice]

def main():
    parser = argparse.ArgumentParser(
        description="Datamosh CLI (OpenCV + PyAV). Choose an algorithm with -a."
    )
    parser.add_argument("-f", "--file", help="Input file path OR comma-separated list for multi-clip algos")
    parser.add_argument("-a", "--algorithm", required=True, choices=sorted(ALGORITHMS.keys()),
                        help="Datamosh algorithm name")
    parser.add_argument("-o", "--output", default=None, help="Output file path")

    # Common optional knobs
    parser.add_argument("--alpha", type=float, default=0.85, help="[flow_leaky] Leaky accumulator 0..1")
    parser.add_argument("--block", type=int, default=16, help="[blockmatch_basic] Block size (px)")
    parser.add_argument("--radius", type=int, default=8, help="[blockmatch_basic] Search radius (px)")
    parser.add_argument("--gop", type=int, default=250, help="[GOP algos] Encoder GOP size hint")
    parser.add_argument("--codec", type=str, default="libx264", help="[GOP algos] Encoder (e.g. libx264, h264_videotoolbox)")
    parser.add_argument("--videosrc", type=str, default="videosrc", help="Folder to scan when using --scan or when -f omitted")
    parser.add_argument("--scan", action="store_true", help="Scan videosrc and interactively select/order files")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logs")
    parser.add_argument("--image", type=str, default=None,
                        help="[video_to_image_mosh] Path to still image to smear into")
    parser.add_argument("--img_dur", type=float, default=3.0,
                        help="[video_to_image_mosh] Duration (seconds) of the image motion clip")
    parser.add_argument("--kb", type=str, default="rotate", choices=["rotate","zoom_in"],
                        help="[video_to_image_mosh] Motion style for the image clip")
    parser.add_argument("--postcut", type=int, default=6,
                        help="[video_to_image_mosh & UI] Drop N frames after each boundary I (stronger smear)")
    parser.add_argument("--mosh_q", type=int, default=8,
                    help="[mosh] MPEG-4 quantizer (higher = blockier = stronger smear); e.g., 6–12")
    parser.add_argument("--pframe_dup_start", type=float, default=None,
                    help="[avidemux_style] Start P-frame duplication after this timestamp (seconds). None = from beginning")
    parser.add_argument("--chunk_length", type=float, default=2.0,
                    help="[randomizer] Duration of each chunk in seconds (default: 2.0)")
    parser.add_argument("--blend_mode", type=str, default="overlay",
                        choices=["overlay", "add", "subtract", "darken", "lighten"],
                        help="[double_exposure] Blend mode")
    parser.add_argument("--opacity", type=float, default=0.5,
                        help="[double_exposure] Opacity for clip B (0..1). 0.5 = 50/50 blend")
    parser.add_argument("--descartes", action="store_true",
                        help="[double_exposure] Blend every pair in videosrc, output one per pair")
    parser.add_argument("--color_preset", type=str, default="urban_grit",
                        choices=["urban_grit", "dirty_glass", "faded_teal_amber", "hard_shadow_split"],
                        help="[color_fx_ffmpeg] Color treatment preset")
    parser.add_argument("--fx_strength", type=float, default=1.0,
                        help="[color_fx_ffmpeg] Effect strength (0.0-2.0)")
    parser.add_argument("--grain", type=int, default=-1,
                        help="[color_fx_ffmpeg] Film grain amount 0-60 (-1 uses preset default)")
    parser.add_argument("--vignette", type=float, default=-1.0,
                        help="[color_fx_ffmpeg] Vignette intensity 0.0-2.0 (-1 uses preset default)")
    parser.add_argument("--ghost", type=float, default=-1.0,
                        help="[color_fx_ffmpeg] Ghost/trail blend 0.0-1.0 (-1 uses preset default)")
    parser.add_argument("--keep_audio", dest="keep_audio", action="store_true", default=True,
                        help="[color_fx_ffmpeg] Keep audio in output (default)")
    parser.add_argument("--no_keep_audio", dest="keep_audio", action="store_false",
                        help="[color_fx_ffmpeg] Disable audio in output")

    args = parser.parse_args()

    # Algorithms that take multiple inputs
    multi_algos = {"gop_multi_drop_concat", "bergman_style", "avidemux_style", "double_exposure"}

    # Resolve inputs
    in_arg = args.file
    if args.algorithm == "double_exposure" and getattr(args, "descartes", False):
        in_path = ""
    elif args.algorithm in multi_algos:
        if in_arg and not args.scan:
            # comma-separated paths
            input_list = [p.strip() for p in in_arg.split(",") if p.strip()]
            missing = [p for p in input_list if not os.path.exists(p)]
            if missing:
                print(f"[ERR] Missing input(s): {', '.join(missing)}", file=sys.stderr)
                sys.exit(1)
            inputs = [os.path.abspath(p) for p in input_list]
        else:
            # scan and interactive order
            vids = scan_videos(args.videosrc)
            if not vids:
                print(f"[ERR] No videos found in '{args.videosrc}'. Drop files with extensions {VIDEO_EXTS} and retry.", file=sys.stderr)
                sys.exit(1)
            chosen = prompt_pick_order(vids, multi=True)
            inputs = [os.path.abspath(p) for p in chosen]
        in_path = ",".join(inputs)
    else:
        if in_arg and not args.scan:
            if not os.path.exists(in_arg):
                print(f"[ERR] Input not found: {in_arg}", file=sys.stderr)
                sys.exit(1)
            in_path = os.path.abspath(in_arg)
        else:
            vids = scan_videos(args.videosrc)
            if not vids:
                print(f"[ERR] No videos found in '{args.videosrc}'. Drop files with extensions {VIDEO_EXTS} and retry.", file=sys.stderr)
                sys.exit(1)
            chosen = prompt_pick_order(vids, multi=False)
            in_path = os.path.abspath(chosen[0])

    # Output path (special-case inspector)
    out_path = args.output
    if not out_path:
        if args.algorithm == "inspect_gop":
            root, _ = os.path.splitext(in_path.split(",")[0])
            out_path = f"{root}.gop.csv"
        else:
            # If multi, use first input for naming
            name_root = in_path.split(",")[0] if "," in in_path else in_path
            out_path = default_output_path(name_root, args.algorithm)
    out_path = os.path.abspath(out_path)

    if args.verbose:
        print(f"[INFO] Algorithm: {args.algorithm}")
        print(f"[INFO] Inputs: {in_path}")
        print(f"[INFO] Output: {out_path}")

    func = ALGORITHMS[args.algorithm]
    try:
        call_params = dict(
            input_path=in_path,
            output_path=out_path,
            alpha=args.alpha,
            block=args.block,
            radius=args.radius,
            gop=args.gop,
            codec=args.codec,
            verbose=args.verbose,
            image=getattr(args, "image", None),
            img_dur=getattr(args, "img_dur", None),
            kb_mode=getattr(args, "kb", None),
            postcut=getattr(args, "postcut", None),
            blend_mode=getattr(args, "blend_mode", None),
            opacity=getattr(args, "opacity", None),
            descartes=getattr(args, "descartes", None),
            videosrc=getattr(args, "videosrc", None),
            color_preset=getattr(args, "color_preset", None),
            fx_strength=getattr(args, "fx_strength", None),
            grain=getattr(args, "grain", None),
            vignette=getattr(args, "vignette", None),
            ghost=getattr(args, "ghost", None),
            keep_audio=getattr(args, "keep_audio", None),
        )

        call_params.update({
            "postcut": args.postcut,
            "mosh_q": args.mosh_q,
            "pframe_dup_start": args.pframe_dup_start,
            "chunk_length": args.chunk_length,
        })

        # keep only the params this function actually declares
        sig = inspect.signature(func)
        # Allow None values to be passed through (don't filter them out)
        filtered = {k: v for k, v in call_params.items() if k in sig.parameters}

        func(**filtered)
    except KeyboardInterrupt:
        print("\n[WARN] Interrupted.")
        sys.exit(130)
    except Exception as e:
        print(f"[ERR] {args.algorithm} failed: {e}", file=sys.stderr)
        sys.exit(2)

    if args.verbose:
        print(f"[OK] Wrote {out_path}")

if __name__ == "__main__":
    main()
