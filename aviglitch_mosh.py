#!/usr/bin/env python3
"""
aviglitch_mosh.py - AviGlitch-style datamosh tool

Mimics AviGlitch Ruby gem behavior using PyAV for direct AVI packet manipulation.
Works best with MPEG-4 ASP (Xvid) AVI files with long GOP and no B-frames.

Two main techniques:
1. I-frame removal - drop keyframes in time window to create smear effects
2. P-frame duplication - duplicate non-key packets to create bloom/echo effects
"""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import av
except ImportError:
    print("[ERROR] PyAV is not installed. Install with: pip install av", file=sys.stderr)
    sys.exit(1)


def check_codec(input_path, verbose=False):
    """Check if input uses MPEG-4 ASP / Xvid codec."""
    try:
        container = av.open(str(input_path))
        video_stream = next((s for s in container.streams if s.type == "video"), None)

        if not video_stream:
            print("[WARNING] No video stream found", file=sys.stderr)
            return False

        codec_name = video_stream.codec_context.name
        container.close()

        if verbose:
            print(f"[INFO] Detected codec: {codec_name}")

        if codec_name not in ('mpeg4', 'xvid'):
            print(f"[WARNING] Codec '{codec_name}' is not MPEG-4 ASP/Xvid", file=sys.stderr)
            print(f"[WARNING] Results may be unpredictable. Use --prep to convert.", file=sys.stderr)
            return False

        return True

    except Exception as e:
        print(f"[ERROR] Failed to check codec: {e}", file=sys.stderr)
        return False


def prep_video(input_path, output_path, q=3, gop=300, fps=24, verbose=False):
    """
    Convert video to glitch-friendly AVI (MPEG-4 ASP, long GOP, no B-frames).
    Mirrors convert_to_xvid.sh behavior.
    """
    print(f"\n=== Prep: Converting to MPEG-4 ASP AVI ===")
    print(f"Input: {input_path}")
    print(f"Quality: q={q}, GOP: {gop}, FPS: {fps}")

    # Check for libxvid encoder
    result = subprocess.run(
        ['ffmpeg', '-hide_banner', '-v', 'error', '-h', 'encoder=libxvid'],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    use_libxvid = result.returncode == 0

    if use_libxvid:
        vcodec = ['-c:v', 'libxvid', '-qscale:v', str(q)]
        print("Using libxvid encoder")
    else:
        vcodec = ['-c:v', 'mpeg4', '-vtag', 'XVID', '-qscale:v', str(q)]
        print("Using mpeg4 encoder with XVID tag")

    cmd = [
        'ffmpeg', '-y', '-i', str(input_path),
        '-vf', f'scale=trunc(iw/2)*2:trunc(ih/2)*2,fps={fps}',
        '-r', str(fps), '-vsync', 'cfr',
    ]
    cmd.extend(vcodec)
    cmd.extend([
        '-g', str(gop),
        '-bf', '0',  # No B-frames
        '-sc_threshold', '0',  # No scene cut detection
        '-pix_fmt', 'yuv420p',
        '-an',  # No audio for prep (keeps it simple)
        str(output_path)
    ])

    if not verbose:
        cmd.insert(1, '-loglevel')
        cmd.insert(2, 'error')

    result = subprocess.run(cmd)

    if result.returncode != 0:
        print(f"[ERROR] Prep conversion failed", file=sys.stderr)
        sys.exit(1)

    print(f"✓ Prepped AVI: {output_path}")
    return output_path


def get_video_duration(container, video_stream):
    """Get video duration in seconds."""
    if video_stream.duration:
        return float(video_stream.duration * video_stream.time_base)
    elif container.duration:
        return float(container.duration) / av.time_base
    return None


def remove_iframes_and_duplicate_pframes(
    input_path,
    output_path,
    drop_start=None,
    drop_end=None,
    dup_at=None,
    dup_count=12,
    keep_first_iframe=True,
    verbose=False
):
    """
    AviGlitch-style packet manipulation:
    1. Remove I-frames in [drop_start, drop_end] window
    2. Duplicate P-frames starting at dup_at timestamp
    """

    print(f"\n=== AviGlitch-Style Packet Mosh ===")
    print(f"Input: {input_path}")
    print(f"Output: {output_path}")

    in_container = av.open(str(input_path))
    video_stream = next((s for s in in_container.streams if s.type == "video"), None)

    if not video_stream:
        print("[ERROR] No video stream found", file=sys.stderr)
        in_container.close()
        sys.exit(1)

    # Get duration for validation
    duration = get_video_duration(in_container, video_stream)
    if verbose and duration:
        print(f"[INFO] Video duration: {duration:.2f}s")

    # Validate parameters
    if drop_start is not None and drop_end is not None:
        if drop_start >= drop_end:
            print("[ERROR] drop_start must be less than drop_end", file=sys.stderr)
            in_container.close()
            sys.exit(1)
        print(f"I-frame removal: {drop_start:.2f}s - {drop_end:.2f}s")

    if dup_at is not None:
        print(f"P-frame duplication: at {dup_at:.2f}s, count: {dup_count}")

    # Open output container
    out_container = av.open(str(output_path), mode='w')

    # Add video stream with same codec
    out_video_stream = out_container.add_stream(video_stream.codec_context.name)
    out_video_stream.width = video_stream.codec_context.width
    out_video_stream.height = video_stream.codec_context.height
    out_video_stream.pix_fmt = video_stream.codec_context.pix_fmt
    out_video_stream.time_base = video_stream.time_base

    # Copy audio stream if present
    audio_stream = next((s for s in in_container.streams if s.type == "audio"), None)
    out_audio_stream = None
    if audio_stream:
        out_audio_stream = out_container.add_stream(audio_stream.codec_context.name)
        out_audio_stream.rate = audio_stream.rate
        out_audio_stream.layout = audio_stream.layout
        out_audio_stream.time_base = audio_stream.time_base
        if verbose:
            print("[INFO] Copying audio stream")

    time_base = video_stream.time_base
    fps = video_stream.average_rate or 24
    # Calculate frame ticks: how many time_base ticks per frame
    from fractions import Fraction
    frame_ticks = max(1, int(round((Fraction(1, 1) / fps) / time_base)))

    if verbose:
        print(f"[INFO] Time base: {time_base}, FPS: {fps}, Frame ticks: {frame_ticks}")

    # Monotonic timestamp counters (in time_base ticks)
    next_pts = None
    next_dts = None

    first_keyframe_seen = False
    iframes_dropped = 0
    pframes_duplicated = 0
    dup_done = False
    total_packets = 0

    def stamp_and_mux(pkt):
        """Stamp packet with monotonic timestamps and mux."""
        nonlocal next_pts, next_dts

        # Initialize counters from first packet if needed
        if next_pts is None or next_dts is None:
            base = pkt.pts if pkt.pts is not None else 0
            next_pts = base
            next_dts = pkt.dts if pkt.dts is not None else base

        # Get incoming timestamps or generate new ones
        in_pts = pkt.pts if pkt.pts is not None else next_pts + frame_ticks
        in_dts = pkt.dts if pkt.dts is not None else next_dts + frame_ticks

        # Enforce strictly increasing timestamps for the muxer
        in_dts = max(in_dts, next_dts + frame_ticks)
        in_pts = max(in_pts, next_pts + frame_ticks)

        pkt.pts = in_pts
        pkt.dts = in_dts
        pkt.time_base = time_base
        pkt.stream = out_video_stream

        out_container.mux(pkt)
        next_pts = in_pts
        next_dts = in_dts

    # Process video packets
    for packet in in_container.demux(video_stream):
        if packet.dts is None and packet.pts is None:
            # Skip header/flush packets
            continue

        total_packets += 1
        t = float((packet.dts if packet.dts is not None else 0) * time_base)

        # --- I-frame removal logic ---
        if packet.is_keyframe:
            # Always keep first keyframe
            if not first_keyframe_seen:
                first_keyframe_seen = True
                if verbose:
                    print(f"[INFO] Keeping first I-frame at t={t:.3f}s")
            # Drop keyframes in window (unless keep_first and it's the first)
            elif drop_start is not None and drop_end is not None:
                if drop_start <= t <= drop_end:
                    iframes_dropped += 1
                    if verbose:
                        print(f"[DROP] I-frame at t={t:.3f}s")
                    continue

        # Write original packet with proper timestamps
        stamp_and_mux(packet)

        # --- P-frame duplication logic ---
        # Check if this is the packet to duplicate
        should_duplicate = (not dup_done and
                          dup_at is not None and
                          t >= dup_at and
                          not packet.is_keyframe)

        if should_duplicate:
            if verbose:
                print(f"[DUP] P-frame duplication at t={t:.3f}s, creating {dup_count} duplicates")

            # Create duplicates with advancing timestamps
            dup_count_actual = min(dup_count, 20)  # Limit to prevent excessive duplication

            # Get packet data for duplication
            packet_data = bytes(packet)

            for i in range(dup_count_actual):
                # Create a new packet with same data but new timestamps
                dup = av.Packet(len(packet_data))
                # Write data into packet buffer
                dup_buffer = memoryview(dup)
                dup_buffer[:] = packet_data

                # Set timestamps - advance by one frame each time
                dup.pts = next_pts + frame_ticks
                dup.dts = next_dts + frame_ticks
                dup.time_base = time_base
                dup.stream = out_video_stream

                out_container.mux(dup)
                next_pts = dup.pts
                next_dts = dup.dts
                pframes_duplicated += 1

            dup_done = True

    # Copy audio packets
    if audio_stream and out_audio_stream:
        in_container.seek(0)  # Reset to beginning
        for packet in in_container.demux(audio_stream):
            packet.stream = out_audio_stream
            out_container.mux(packet)

    out_container.close()
    in_container.close()

    # Summary
    print(f"\n=== Summary ===")
    print(f"Total video packets: {total_packets}")
    print(f"I-frames dropped: {iframes_dropped}")
    print(f"P-frames duplicated: {pframes_duplicated}")
    print(f"✓ Output: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description='AviGlitch-style datamosh tool using PyAV',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # I-frame removal (smear effect)
  python aviglitch_mosh.py --in input.avi --out moshed.avi \\
    --drop-start 5.0 --drop-end 7.0

  # P-frame duplication (bloom effect)
  python aviglitch_mosh.py --in input.avi --out moshed.avi \\
    --dup-at 6.0 --dup-count 12

  # Combined effect
  python aviglitch_mosh.py --in input.avi --out moshed.avi \\
    --drop-start 5.0 --drop-end 7.0 --dup-at 6.0 --dup-count 12

  # Auto-convert input first (prep)
  python aviglitch_mosh.py --in input.mp4 --out moshed.avi \\
    --prep --drop-start 3.0 --drop-end 5.0

  # Custom prep settings
  python aviglitch_mosh.py --in input.mp4 --out moshed.avi \\
    --prep --prep-q 5 --prep-gop 600 --prep-fps 30 \\
    --drop-start 2.0 --drop-end 4.0

Note: Works best with MPEG-4 ASP (Xvid) AVI input with long GOP and no B-frames.
      Use --prep to auto-convert any video format to glitch-friendly AVI.
        """
    )

    parser.add_argument('--in', dest='input', required=True,
                        help='Input AVI file (or any format with --prep)')
    parser.add_argument('--out', dest='output', required=True,
                        help='Output AVI file')

    # I-frame removal options
    parser.add_argument('--drop-start', type=float, default=None,
                        help='Start of I-frame drop window in seconds')
    parser.add_argument('--drop-end', type=float, default=None,
                        help='End of I-frame drop window in seconds')
    parser.add_argument('--keep-first-iframe', action='store_true', default=True,
                        help='Always keep first I-frame (default: true)')

    # P-frame duplication options
    parser.add_argument('--dup-at', type=float, default=None,
                        help='Timestamp (seconds) to start P-frame duplication')
    parser.add_argument('--dup-count', type=int, default=12,
                        help='Number of P-frame duplications (default: 12)')

    # Prep options
    parser.add_argument('--prep', action='store_true',
                        help='Auto-convert input to MPEG-4 ASP AVI before moshing')
    parser.add_argument('--prep-q', type=int, default=3,
                        help='Quality for prep conversion (1-31, lower=better) (default: 3)')
    parser.add_argument('--prep-gop', type=int, default=300,
                        help='GOP length for prep conversion (default: 300)')
    parser.add_argument('--prep-fps', type=int, default=24,
                        help='FPS for prep conversion (default: 24)')

    # General options
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Verbose output')

    args = parser.parse_args()

    # Validate inputs
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"[ERROR] Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Validate at least one operation specified
    has_drop = args.drop_start is not None and args.drop_end is not None
    has_dup = args.dup_at is not None

    if not has_drop and not has_dup:
        print("[ERROR] Must specify at least one operation:", file=sys.stderr)
        print("  - I-frame removal: --drop-start and --drop-end", file=sys.stderr)
        print("  - P-frame duplication: --dup-at", file=sys.stderr)
        sys.exit(1)

    # Handle prep conversion
    work_input = input_path
    temp_prep = None

    if args.prep:
        # Create temp file for prepped AVI
        temp_prep = tempfile.NamedTemporaryFile(suffix='.avi', delete=False)
        temp_prep_path = Path(temp_prep.name)
        temp_prep.close()

        prep_video(
            input_path,
            temp_prep_path,
            q=args.prep_q,
            gop=args.prep_gop,
            fps=args.prep_fps,
            verbose=args.verbose
        )
        work_input = temp_prep_path
    else:
        # Check codec if not prepping
        check_codec(input_path, args.verbose)

    # Perform datamosh operations
    try:
        remove_iframes_and_duplicate_pframes(
            work_input,
            output_path,
            drop_start=args.drop_start,
            drop_end=args.drop_end,
            dup_at=args.dup_at,
            dup_count=args.dup_count,
            keep_first_iframe=args.keep_first_iframe,
            verbose=args.verbose
        )
    finally:
        # Cleanup temp prep file
        if temp_prep and temp_prep_path.exists():
            os.unlink(temp_prep_path)
            if args.verbose:
                print(f"[INFO] Cleaned up temp prep file")


if __name__ == '__main__':
    main()
