#!/usr/bin/env python3
"""
mosh - P-cascade bloom datamosh transition generator

Creates a heavy "P-cascade bloom" datamosh transition (big smear) between two clips.
Uses packet-level surgery (no re-encode) for maximum artifact strength.
"""

import argparse
import subprocess
import sys
import os
import json
import tempfile
import shutil
import random
from pathlib import Path

try:
    import av
except ImportError:
    print("Error: PyAV not installed. Run: pip install av", file=sys.stderr)
    sys.exit(1)


def run_ffmpeg(cmd, description, verbose=False):
    """Execute FFmpeg command with error handling."""
    print(f"\n[{description}]")
    if verbose:
        print(f"Command: {' '.join(cmd)}")

    loglevel = "info" if verbose else "error"
    if cmd[0] == "ffmpeg" and "-loglevel" not in cmd:
        cmd = cmd[:1] + ["-hide_banner", "-loglevel", loglevel] + cmd[1:]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error: {description} failed", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)

    if verbose and result.stderr:
        print(result.stderr)

    return result


def get_duration(video_path):
    """Get video duration in seconds using ffprobe."""
    cmd = [
        'ffprobe',
        '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'json',
        str(video_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error: Failed to probe {video_path}", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)

    data = json.loads(result.stdout)
    return float(data['format']['duration'])


def get_fps(video_path):
    """Get video framerate using ffprobe."""
    cmd = [
        'ffprobe',
        '-v', 'error',
        '-select_streams', 'v:0',
        '-show_entries', 'stream=avg_frame_rate',
        '-of', 'json',
        str(video_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return 30.0

    data = json.loads(result.stdout)
    fr = data['streams'][0].get('avg_frame_rate', '30/1')
    try:
        n, d = fr.split('/')
        fps = float(n) / float(d) if d != '0' else 30.0
    except Exception:
        fps = 30.0
    return fps


def validate_inputs(clip_a, clip_b):
    """Validate that input files exist."""
    if not Path(clip_a).exists():
        print(f"Error: Input file not found: {clip_a}", file=sys.stderr)
        sys.exit(1)
    if not Path(clip_b).exists():
        print(f"Error: Input file not found: {clip_b}", file=sys.stderr)
        sys.exit(1)


def check_libxvid():
    """Check if libxvid encoder is available."""
    try:
        result = subprocess.run(
            ['ffmpeg', '-hide_banner', '-v', 'error', '-h', 'encoder=libxvid'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        return result.returncode == 0
    except Exception:
        return False


def to_xvid(input_path, output_path, fps, width, q, gop_len, hold_sec=0, is_last=False, verbose=False):
    """Convert clip to Xvid AVI with optional freeze hold at end."""
    # Build filter chain
    vf = f"scale=trunc(iw/2)*2:trunc(ih/2)*2,scale={width}:-2,fps={fps}"
    if hold_sec > 0 and not is_last:
        vf += f",tpad=stop_mode=clone:stop_duration={float(hold_sec)}"

    # Use libxvid if available, otherwise mpeg4 with XVID tag
    if check_libxvid():
        vcodec = ["-c:v", "libxvid"]
    else:
        vcodec = ["-c:v", "mpeg4", "-vtag", "XVID"]

    cmd = [
        'ffmpeg', '-y', '-i', str(input_path),
        '-an', '-vf', vf, '-r', str(fps),
        *vcodec, '-qscale:v', str(q),
        '-g', str(min(gop_len, 600)), '-bf', '0', '-sc_threshold', '0',
        '-pix_fmt', 'yuv420p',
        str(output_path)
    ]

    run_ffmpeg(cmd, f"Converting {Path(input_path).name} to Xvid AVI", verbose)


def concat_copy(avi_list, output_path, verbose=False):
    """Concatenate AVI files using codec copy (no re-encode)."""
    list_file = output_path.parent / 'concat_list.txt'
    with open(list_file, 'w') as f:
        for avi_path in avi_list:
            # Escape single quotes for concat demuxer
            escaped = str(avi_path).replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")

    cmd = [
        'ffmpeg', '-y',
        '-f', 'concat',
        '-safe', '0',
        '-i', str(list_file),
        '-map', '0:v:0',
        '-c', 'copy',
        '-an',
        str(output_path)
    ]

    run_ffmpeg(cmd, "Concatenating clips (codec copy)", verbose)
    list_file.unlink()


def packet_surgery(input_avi, output_avi, join_time_sec, no_iframe_window, postcut, verbose=False):
    """
    Remove I-frames at packet level in the window [join_time, join_time + no_iframe_window].
    Also drop 'postcut' packets after each removed I-frame.
    NO re-encode = maximum glitch preservation.
    """
    print(f"\n=== Packet Surgery: Stripping I-frames ===")
    print(f"Window: [{join_time_sec:.3f}s, {join_time_sec + no_iframe_window:.3f}s]")
    print(f"Postcut: {postcut} packets after each removed I-frame")

    in_ct = av.open(str(input_avi))
    vin = next(s for s in in_ct.streams if s.type == "video")

    out_ct = av.open(str(output_avi), mode="w")

    # Create output stream
    try:
        vout = out_ct.add_stream(template=vin)
    except TypeError:
        codec_name = getattr(getattr(vin, "codec_context", None), "name", None) or "mpeg4"
        vout = out_ct.add_stream(codec_name)
        try:
            vout.time_base = vin.time_base
        except Exception:
            pass
        try:
            vout.codec_context.extradata = vin.codec_context.extradata
        except Exception:
            pass

    try:
        vout.codec_tag = vin.codec_tag
    except Exception:
        pass

    keep_first_i = True
    pending_drop = 0
    removed_count = 0
    total_packets = 0

    window_start = join_time_sec
    window_end = join_time_sec + no_iframe_window

    for pkt in in_ct.demux(vin):
        total_packets += 1
        pkt_time = float(pkt.pts * vin.time_base) if pkt.pts is not None else 0

        if pkt.is_keyframe:
            if keep_first_i:
                # Always keep the very first I-frame
                keep_first_i = False
                try:
                    pkt.rescale_ts(vin.time_base, vout.time_base)
                except Exception:
                    pass
                pkt.stream = vout
                out_ct.mux(pkt)
            elif window_start <= pkt_time <= window_end:
                # Drop I-frame in window
                removed_count += 1
                pending_drop = postcut
                if verbose:
                    print(f"  Dropped I-frame at {pkt_time:.3f}s (packet #{total_packets})")
            else:
                # Keep I-frame outside window
                try:
                    pkt.rescale_ts(vin.time_base, vout.time_base)
                except Exception:
                    pass
                pkt.stream = vout
                out_ct.mux(pkt)
        else:
            # P or B frame
            if pending_drop > 0:
                pending_drop -= 1
                if verbose:
                    print(f"  Dropped packet (postcut) at {pkt_time:.3f}s")
            else:
                try:
                    pkt.rescale_ts(vin.time_base, vout.time_base)
                except Exception:
                    pass
                pkt.stream = vout
                out_ct.mux(pkt)

    out_ct.close()
    in_ct.close()

    print(f"Removed {removed_count} I-frames from {total_packets} total packets")


def repeat_smear_segment(input_avi, output_avi, join_time_sec, repeat_boost, repeat_times, verbose=False):
    """
    Repeat a segment after the join point to amplify smear.
    Uses FFmpeg to extract and repeat segments, then concat with codec copy.
    """
    print(f"\n=== Smear Boost: Repeating segment ===")
    print(f"Segment: [{join_time_sec:.3f}s, {join_time_sec + repeat_boost:.3f}s] × {repeat_times}")

    temp_dir = Path(input_avi).parent
    segment_start = join_time_sec
    segment_end = join_time_sec + repeat_boost

    # Extract three segments using FFmpeg
    before_seg = temp_dir / 'before_seg.avi'
    repeat_seg = temp_dir / 'repeat_seg.avi'
    after_seg = temp_dir / 'after_seg.avi'

    # Before segment: [0, segment_start]
    cmd_before = [
        'ffmpeg', '-y', '-i', str(input_avi),
        '-ss', '0',
        '-t', str(segment_start),
        '-c', 'copy',
        str(before_seg)
    ]
    run_ffmpeg(cmd_before, "Extracting before segment", verbose)

    # Repeat segment: [segment_start, segment_end]
    cmd_repeat = [
        'ffmpeg', '-y', '-i', str(input_avi),
        '-ss', str(segment_start),
        '-t', str(repeat_boost),
        '-c', 'copy',
        str(repeat_seg)
    ]
    run_ffmpeg(cmd_repeat, "Extracting repeat segment", verbose)

    # After segment: [segment_end, end]
    cmd_after = [
        'ffmpeg', '-y', '-i', str(input_avi),
        '-ss', str(segment_end),
        '-c', 'copy',
        str(after_seg)
    ]
    run_ffmpeg(cmd_after, "Extracting after segment", verbose)

    # Build concat list: before + repeat*N + after
    concat_list = temp_dir / 'smear_concat.txt'
    with open(concat_list, 'w') as f:
        f.write(f"file '{before_seg.absolute()}'\n")
        for _ in range(repeat_times):
            f.write(f"file '{repeat_seg.absolute()}'\n")
        f.write(f"file '{after_seg.absolute()}'\n")

    # Concat all segments
    cmd_concat = [
        'ffmpeg', '-y',
        '-f', 'concat',
        '-safe', '0',
        '-i', str(concat_list),
        '-c', 'copy',
        str(output_avi)
    ]
    run_ffmpeg(cmd_concat, f"Concatenating segments (1 + {repeat_times} + 1)", verbose)

    # Clean up temp files
    before_seg.unlink(missing_ok=True)
    repeat_seg.unlink(missing_ok=True)
    after_seg.unlink(missing_ok=True)
    concat_list.unlink(missing_ok=True)

    print(f"Smear boost complete")


def encode_h264(input_path, output_path, verbose=False):
    """Encode final output to H.264 MP4."""
    cmd = [
        'ffmpeg', '-y',
        '-i', str(input_path),
        '-c:v', 'libx264',
        '-crf', '18',
        '-pix_fmt', 'yuv420p',
        str(output_path)
    ]
    run_ffmpeg(cmd, "Encoding to H.264 MP4", verbose)


def main():
    parser = argparse.ArgumentParser(
        description='Create heavy P-cascade bloom datamosh transition between two clips (packet surgery mode)',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parser.add_argument('--a', required=True, help='First clip path')
    parser.add_argument('--b', required=True, help='Second clip path')
    parser.add_argument('--fps', type=int, default=30, help='Target framerate')
    parser.add_argument('--width', type=int, default=1280, help='Target width (height auto)')
    parser.add_argument('--q', type=int, default=10, help='Xvid quantizer (higher=blockier, 1-31, try 8-14)')
    parser.add_argument('--gop-len', type=int, default=9999, help='GOP length')
    parser.add_argument('--no-iframe-window', type=float, default=2.0,
                       help='Duration (seconds) after join to strip I-frames')
    parser.add_argument('--postcut', type=int, default=12,
                       help='Packets to drop after each removed I-frame (higher=stronger smear)')
    parser.add_argument('--repeat-boost', type=float, default=0.5,
                       help='Duration (seconds) after join to repeat for smear boost')
    parser.add_argument('--repeat-times', type=int, default=5,
                       help='Number of times to repeat the boost segment')
    parser.add_argument('-v', '--verbose', action='store_true',
                       help='Verbose output')

    args = parser.parse_args()

    # Validate inputs
    print("=== Validating inputs ===")
    validate_inputs(args.a, args.b)

    # Create temp working directory
    temp_dir = Path(tempfile.mkdtemp(prefix='mosh_'))
    print(f"\nWorking directory: {temp_dir}")

    try:
        # Step 1: Convert to Xvid AVI
        print("\n=== Step 1: Converting to Xvid AVI ===")
        xvid_a = temp_dir / 'A.avi'
        xvid_b = temp_dir / 'B.avi'

        to_xvid(args.a, xvid_a, args.fps, args.width, args.q, args.gop_len, verbose=args.verbose)
        to_xvid(args.b, xvid_b, args.fps, args.width, args.q, args.gop_len, verbose=args.verbose)

        # Step 2: Probe join time
        print("\n=== Step 2: Probing join time ===")
        join_t = get_duration(xvid_a)
        print(f"JOIN_T = {join_t:.3f}s")

        # Step 3: Concatenate with codec copy
        print("\n=== Step 3: Concatenating (codec copy) ===")
        out_longgop = Path.cwd() / 'out_longgop.avi'
        concat_copy([xvid_a, xvid_b], out_longgop, verbose=args.verbose)

        total_duration = get_duration(out_longgop)
        print(f"Total duration = {total_duration:.3f}s")

        # Validate windows
        if join_t + args.no_iframe_window > total_duration:
            print(f"Warning: no_iframe_window extends beyond video end", file=sys.stderr)
            args.no_iframe_window = total_duration - join_t

        if join_t + args.repeat_boost > total_duration:
            print(f"Warning: repeat_boost extends beyond video end", file=sys.stderr)
            args.repeat_boost = total_duration - join_t

        # Step 4: Packet surgery - strip I-frames
        print("\n=== Step 4: Packet surgery ===")
        mosh_core = Path.cwd() / 'mosh_core.avi'
        packet_surgery(out_longgop, mosh_core, join_t, args.no_iframe_window, args.postcut, verbose=args.verbose)

        # Step 5: Amplify smear by repeating segment
        print("\n=== Step 5: Smear boost ===")
        mosh_final_avi = Path.cwd() / 'mosh_final.avi'
        repeat_smear_segment(mosh_core, mosh_final_avi, join_t, args.repeat_boost, args.repeat_times, verbose=args.verbose)

        # Step 6: Encode to H.264 MP4
        print("\n=== Step 6: Encoding final MP4 ===")
        mosh_final_mp4 = Path.cwd() / 'mosh_final.mp4'
        encode_h264(mosh_final_avi, mosh_final_mp4, verbose=args.verbose)

        print("\n=== Success! ===")
        print(f"Outputs:")
        print(f"  - {out_longgop} (long GOP concat)")
        print(f"  - {mosh_core} (I-frames stripped)")
        print(f"  - {mosh_final_avi} (smear boosted)")
        print(f"  - {mosh_final_mp4} (H.264 delivery)")

    finally:
        # Clean up temp directory
        print(f"\nCleaning up: {temp_dir}")
        shutil.rmtree(temp_dir)


if __name__ == '__main__':
    main()
