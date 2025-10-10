#!/usr/bin/env python3
"""
mosh_h264 - H.264 long-GOP datamosh with IDR strip and SEI removal

Modern H.264-based datamosh using packet surgery:
- Long GOP encoding (9999+ frames between keyframes)
- Strip IDR frames at join points for P-frame cascade
- Remove SEI messages that could trigger decoder resets
- Better compatibility and compression than MPEG-4 ASP
"""

import argparse
import subprocess
import sys
import os
import json
import tempfile
import shutil
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


def validate_inputs(clip_a, clip_b):
    """Validate that input files exist."""
    if not Path(clip_a).exists():
        print(f"Error: Input file not found: {clip_a}", file=sys.stderr)
        sys.exit(1)
    if not Path(clip_b).exists():
        print(f"Error: Input file not found: {clip_b}", file=sys.stderr)
        sys.exit(1)


def normalize_to_h264(input_path, output_path, fps, width, crf, gop_len, verbose=False):
    """Normalize clip to H.264 with very long GOP."""
    cmd = [
        'ffmpeg', '-y', '-i', str(input_path),
        '-vf', f'fps={fps},scale={width}:-2,format=yuv420p',
        '-c:v', 'libx264',
        '-crf', str(crf),
        '-g', str(gop_len),
        '-bf', '0',  # No B-frames
        '-sc_threshold', '0',  # Disable scene cut detection
        '-x264-params', 'keyint=9999:min-keyint=9999',  # Force very long GOP
        '-an',
        str(output_path)
    ]
    run_ffmpeg(cmd, f"Normalizing {Path(input_path).name} to H.264 long-GOP", verbose)


def concat_copy(h264_list, output_path, verbose=False):
    """Concatenate H.264 files using concat demuxer."""
    list_file = output_path.parent / 'concat_list.txt'
    with open(list_file, 'w') as f:
        for h264_path in h264_list:
            escaped = str(h264_path).replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")

    cmd = [
        'ffmpeg', '-y',
        '-f', 'concat',
        '-safe', '0',
        '-i', str(list_file),
        '-c', 'copy',
        str(output_path)
    ]

    run_ffmpeg(cmd, "Concatenating H.264 clips (codec copy)", verbose)
    list_file.unlink()


def is_idr_frame(packet):
    """
    Check if packet is an IDR frame (instantaneous decoder refresh).
    IDR frames are synchronization points that reset decoder state.
    """
    # In H.264, IDR frames are keyframes with specific NAL unit type
    # PyAV marks them as keyframes, but not all keyframes are IDR
    if not packet.is_keyframe:
        return False

    # For H.264, we need to check the actual NAL unit type
    # NAL type 5 = IDR frame, type 1 = non-IDR I-frame
    # This is a simplified check - in practice, PyAV doesn't expose NAL types easily
    # So we'll treat all keyframes as potential IDRs for now
    return True


def packet_surgery_h264(input_mp4, output_mp4, join_time_sec, no_iframe_window, postcut, verbose=False):
    """
    H.264 packet surgery: Strip IDR frames in window and remove SEI messages.

    This creates cascading P-frame artifacts by:
    1. Removing IDR (sync) frames in the specified time window
    2. Dropping postcut packets after each removed IDR
    3. Filtering out SEI (supplemental enhancement info) that could reset decoder
    """
    print(f"\n=== H.264 Packet Surgery ===")
    print(f"Window: [{join_time_sec:.3f}s, {join_time_sec + no_iframe_window:.3f}s]")
    print(f"Postcut: {postcut} packets after each removed IDR")

    in_ct = av.open(str(input_mp4))
    vin = next(s for s in in_ct.streams if s.type == "video")

    out_ct = av.open(str(output_mp4), mode="w")

    # Create output stream
    try:
        vout = out_ct.add_stream(template=vin)
    except TypeError:
        # Fallback for older PyAV
        vout = out_ct.add_stream("h264")
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

    keep_first_idr = True
    pending_drop = 0
    removed_count = 0
    total_packets = 0
    sei_removed = 0

    window_start = join_time_sec
    window_end = join_time_sec + no_iframe_window

    for pkt in in_ct.demux(vin):
        total_packets += 1
        pkt_time = float(pkt.pts * vin.time_base) if pkt.pts is not None else 0

        # Check for SEI packets (supplemental enhancement info)
        # SEI can trigger decoder resets, so we filter them out
        # Note: PyAV doesn't directly expose NAL types, so this is a heuristic
        # SEI packets are typically small and not keyframes
        is_sei = not pkt.is_keyframe and pkt.size < 100

        if is_sei:
            sei_removed += 1
            if verbose:
                print(f"  Filtered SEI-like packet at {pkt_time:.3f}s (size: {pkt.size})")
            continue

        if pkt.is_keyframe:
            if keep_first_idr:
                # Always keep the very first IDR frame
                keep_first_idr = False
                try:
                    pkt.rescale_ts(vin.time_base, vout.time_base)
                except Exception:
                    pass
                pkt.stream = vout
                out_ct.mux(pkt)
            elif window_start <= pkt_time <= window_end:
                # Drop IDR frame in window
                removed_count += 1
                pending_drop = postcut
                if verbose:
                    print(f"  Dropped IDR at {pkt_time:.3f}s (packet #{total_packets})")
            else:
                # Keep IDR frame outside window
                try:
                    pkt.rescale_ts(vin.time_base, vout.time_base)
                except Exception:
                    pass
                pkt.stream = vout
                out_ct.mux(pkt)
        else:
            # P or non-keyframe
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

    print(f"Removed {removed_count} IDR frames and {sei_removed} SEI packets from {total_packets} total packets")


def repeat_smear_segment_h264(input_mp4, output_mp4, join_time_sec, repeat_boost, repeat_times, verbose=False):
    """
    Repeat a segment after join point to amplify smear (H.264 version).
    Uses FFmpeg codec-copy to preserve broken references.
    """
    print(f"\n=== Smear Boost: Repeating segment ===")
    print(f"Segment: [{join_time_sec:.3f}s, {join_time_sec + repeat_boost:.3f}s] × {repeat_times}")

    temp_dir = Path(input_mp4).parent
    segment_start = join_time_sec
    segment_end = join_time_sec + repeat_boost

    # Get total duration to validate segments
    total_duration = get_duration(input_mp4)

    # If repeat segment would be empty or invalid, just copy the input
    if segment_start >= total_duration or repeat_boost <= 0.01:
        print(f"Warning: Cannot repeat segment (start={segment_start:.3f}s >= duration={total_duration:.3f}s)")
        print(f"Copying input to output without smear boost")
        shutil.copy(input_mp4, output_mp4)
        return

    # Adjust segment_end if it exceeds duration
    if segment_end > total_duration:
        segment_end = total_duration
        repeat_boost = segment_end - segment_start
        print(f"Adjusted repeat_boost to {repeat_boost:.3f}s (video ends before planned segment)")

    # Extract segments
    before_seg = temp_dir / 'before_seg.mp4'
    repeat_seg = temp_dir / 'repeat_seg.mp4'
    after_seg = temp_dir / 'after_seg.mp4'

    segments_to_concat = []

    # Before: [0, segment_start]
    if segment_start > 0.01:
        cmd_before = [
            'ffmpeg', '-y', '-i', str(input_mp4),
            '-ss', '0',
            '-t', str(segment_start),
            '-c', 'copy',
            str(before_seg)
        ]
        run_ffmpeg(cmd_before, "Extracting before segment", verbose)
        segments_to_concat.append(before_seg)

    # Repeat: [segment_start, segment_end]
    cmd_repeat = [
        'ffmpeg', '-y', '-i', str(input_mp4),
        '-ss', str(segment_start),
        '-t', str(repeat_boost),
        '-c', 'copy',
        str(repeat_seg)
    ]
    run_ffmpeg(cmd_repeat, "Extracting repeat segment", verbose)

    # Add repeat segment N times
    for _ in range(repeat_times):
        segments_to_concat.append(repeat_seg)

    # After: [segment_end, end]
    if segment_end < total_duration - 0.01:
        cmd_after = [
            'ffmpeg', '-y', '-i', str(input_mp4),
            '-ss', str(segment_end),
            '-c', 'copy',
            str(after_seg)
        ]
        run_ffmpeg(cmd_after, "Extracting after segment", verbose)
        segments_to_concat.append(after_seg)

    # Concat - use filter_complex instead of concat demuxer
    # This is more robust when dealing with broken H.264 streams
    if len(segments_to_concat) == 1:
        # Only one segment, just copy it
        shutil.copy(segments_to_concat[0], output_mp4)
    else:
        # Build filter_complex concat
        inputs = []
        for seg in segments_to_concat:
            inputs.extend(['-i', str(seg)])

        # Build concat filter string: [0:v][1:v][2:v]...concat=n=N:v=1:a=0[v]
        concat_inputs = ''.join([f'[{i}:v]' for i in range(len(segments_to_concat))])
        filter_str = f'{concat_inputs}concat=n={len(segments_to_concat)}:v=1:a=0[v]'

        # Note: We have to re-encode here since filter_complex doesn't support codec copy
        # Use high quality H.264 settings to preserve glitches
        cmd_concat = [
            'ffmpeg', '-y',
            *inputs,
            '-filter_complex', filter_str,
            '-map', '[v]',
            '-c:v', 'libx264',
            '-preset', 'ultrafast',  # Fast encoding
            '-qp', '0',  # Lossless to preserve artifacts
            '-pix_fmt', 'yuv420p',
            str(output_mp4)
        ]
        run_ffmpeg(cmd_concat, f"Concatenating {len(segments_to_concat)} segments (re-encode)", verbose)

    # Cleanup
    before_seg.unlink(missing_ok=True)
    repeat_seg.unlink(missing_ok=True)
    after_seg.unlink(missing_ok=True)

    print(f"Smear boost complete")


def main():
    parser = argparse.ArgumentParser(
        description='H.264 long-GOP datamosh with IDR strip and SEI removal',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parser.add_argument('--a', required=True, help='First clip path')
    parser.add_argument('--b', required=True, help='Second clip path')
    parser.add_argument('--fps', type=int, default=30, help='Target framerate')
    parser.add_argument('--width', type=int, default=1280, help='Target width (height auto)')
    parser.add_argument('--crf', type=int, default=23, help='H.264 CRF (lower=higher quality, 18-28)')
    parser.add_argument('--gop-len', type=int, default=9999, help='GOP length')
    parser.add_argument('--no-iframe-window', type=float, default=2.0,
                       help='Duration (seconds) after join to strip IDR frames')
    parser.add_argument('--postcut', type=int, default=12,
                       help='Packets to drop after each removed IDR frame')
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
    temp_dir = Path(tempfile.mkdtemp(prefix='mosh_h264_'))
    print(f"\nWorking directory: {temp_dir}")

    try:
        # Step 1: Normalize to H.264 long-GOP
        print("\n=== Step 1: Normalizing to H.264 long-GOP ===")
        h264_a = temp_dir / 'A.mp4'
        h264_b = temp_dir / 'B.mp4'

        normalize_to_h264(args.a, h264_a, args.fps, args.width, args.crf, args.gop_len, verbose=args.verbose)
        normalize_to_h264(args.b, h264_b, args.fps, args.width, args.crf, args.gop_len, verbose=args.verbose)

        # Step 2: Probe join time
        print("\n=== Step 2: Probing join time ===")
        join_t = get_duration(h264_a)
        print(f"JOIN_T = {join_t:.3f}s")

        # Step 3: Concatenate with codec copy
        print("\n=== Step 3: Concatenating (codec copy) ===")
        out_longgop = Path.cwd() / 'out_longgop_h264.mp4'
        concat_copy([h264_a, h264_b], out_longgop, verbose=args.verbose)

        total_duration = get_duration(out_longgop)
        print(f"Total duration = {total_duration:.3f}s")

        # Validate windows
        if join_t + args.no_iframe_window > total_duration:
            print(f"Warning: no_iframe_window extends beyond video end", file=sys.stderr)
            args.no_iframe_window = total_duration - join_t

        if join_t + args.repeat_boost > total_duration:
            print(f"Warning: repeat_boost extends beyond video end", file=sys.stderr)
            args.repeat_boost = total_duration - join_t

        # Step 4: H.264 packet surgery
        print("\n=== Step 4: H.264 packet surgery ===")
        mosh_core = Path.cwd() / 'mosh_core_h264.mp4'
        packet_surgery_h264(out_longgop, mosh_core, join_t, args.no_iframe_window, args.postcut, verbose=args.verbose)

        # Step 5: Amplify smear by repeating segment
        print("\n=== Step 5: Smear boost ===")
        mosh_final = Path.cwd() / 'mosh_final_h264.mp4'
        repeat_smear_segment_h264(mosh_core, mosh_final, join_t, args.repeat_boost, args.repeat_times, verbose=args.verbose)

        print("\n=== Success! ===")
        print(f"Outputs:")
        print(f"  - {out_longgop} (long GOP concat)")
        print(f"  - {mosh_core} (IDR frames stripped)")
        print(f"  - {mosh_final} (smear boosted)")

    finally:
        # Clean up temp directory
        print(f"\nCleaning up: {temp_dir}")
        shutil.rmtree(temp_dir)


if __name__ == '__main__':
    main()
