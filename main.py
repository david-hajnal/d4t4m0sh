#!/usr/bin/env python3
import argparse
import os
import sys
from algorithms import ALGORITHMS

def default_output_path(in_path: str, algo: str) -> str:
    root, ext = os.path.splitext(in_path)
    if not ext:
        ext = ".mp4"
    return f"{root}.{algo}.mosh{ext}"

def main():
    parser = argparse.ArgumentParser(
        description="Datamosh CLI (OpenCV + PyAV). Choose an algorithm with -a."
    )
    parser.add_argument("-f", "--file", required=True, help="Input video file path")
    parser.add_argument("-a", "--algorithm", required=True, choices=sorted(ALGORITHMS.keys()),
                        help="Datamosh algorithm name")
    parser.add_argument("-o", "--output", default=None, help="Output video file path")

    # Common optional knobs; algorithms ignore those they don't need
    parser.add_argument("--alpha", type=float, default=0.85,
                        help="[flow_leaky] Leaky accumulator factor (0..1)")
    parser.add_argument("--block", type=int, default=16,
                        help="[blockmatch_basic] Block size (pixels)")
    parser.add_argument("--radius", type=int, default=8,
                        help="[blockmatch_basic] Search radius (pixels)")
    parser.add_argument("--gop", type=int, default=250,
                        help="[gop_iframe_drop] Encoder GOP size (hint to libx264)")
    parser.add_argument("--codec", type=str, default="libx264",
                        help="[gop_iframe_drop] Encoder codec (e.g., libx264)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logs")

    args = parser.parse_args()

    in_path = args.file
    if not os.path.exists(in_path):
        print(f"[ERR] Input not found: {in_path}", file=sys.stderr)
        sys.exit(1)

    algo = args.algorithm
    out_path = args.output or default_output_path(in_path, algo)

    if args.verbose:
        print(f"[INFO] Algorithm: {algo}")
        print(f"[INFO] Input: {in_path}")
        print(f"[INFO] Output: {out_path}")

    func = ALGORITHMS[algo]
    try:
        func(
            input_path=in_path,
            output_path=out_path,
            alpha=args.alpha,
            block=args.block,
            radius=args.radius,
            gop=args.gop,
            codec=args.codec,
            verbose=args.verbose,
        )
    except KeyboardInterrupt:
        print("\n[WARN] Interrupted.")
        sys.exit(130)
    except Exception as e:
        print(f"[ERR] {algo} failed: {e}", file=sys.stderr)
        sys.exit(2)

    if args.verbose:
        print(f"[OK] Wrote {out_path}")

if __name__ == "__main__":
    main()
