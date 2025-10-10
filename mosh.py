#!/usr/bin/env python3
"""
mosh - P-cascade bloom datamosh transition generator

Creates a heavy "P-cascade bloom" datamosh transition (big smear) between two clips.
"""

import argparse
import subprocess
import sys
import os
import json
import tempfile
import shutil
from pathlib import Path


def run_ffmpeg(cmd, description):
    """Execute FFmpeg command with error handling."""
    print(f"\n[{description}]")
    print(f"Command: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error: {description} failed", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
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


def normalize_clip(input_path, output_path, fps, width):
    """Normalize clip to identical fps/size/pixfmt with no audio."""
    cmd = [
        'ffmpeg', '-y', '-i', str(input_path),
        '-vf', f'fps={fps},scale={width}:-2,format=yuv420p',
        '-an',
        str(output_path)
    ]
    run_ffmpeg(cmd, f"Normalizing {Path(input_path).name}")


def concat_with_longgop(norm_a, norm_b, output_path, q, gop_len):
    """Concatenate clips with MPEG-4 ASP, no B-frames, long GOP, no scene cuts."""
    cmd = [
        'ffmpeg', '-y',
        '-i', str(norm_a),
        '-i', str(norm_b),
        '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]',
        '-map', '[v]',
        '-c:v', 'mpeg4',
        '-qscale:v', str(q),
        '-bf', '0',
        '-g', str(gop_len),
        '-sc_threshold', '0',
        '-pix_fmt', 'yuv420p',
        str(output_path)
    ]
    run_ffmpeg(cmd, "Concatenating with long GOP")


def strip_iframes_window(input_path, output_path, join_t, no_iframe_window, q, gop_len):
    """Strip I-frames only in the specified time window."""
    join_t_plus = join_t + no_iframe_window

    # Escape the select filter properly
    select_expr = f"select='not(between(t,{join_t},{join_t_plus})*eq(pict_type,I))',setpts=N/FRAME_RATE/TB"

    cmd = [
        'ffmpeg', '-y',
        '-i', str(input_path),
        '-vf', select_expr,
        '-c:v', 'mpeg4',
        '-qscale:v', str(q),
        '-bf', '0',
        '-g', str(gop_len),
        '-sc_threshold', '0',
        '-pix_fmt', 'yuv420p',
        str(output_path)
    ]
    run_ffmpeg(cmd, f"Stripping I-frames in window [{join_t:.3f}, {join_t_plus:.3f}]")


def extract_segment(input_path, output_path, start, duration):
    """Extract a segment from video (re-encode to maintain codec params)."""
    cmd = [
        'ffmpeg', '-y',
        '-ss', str(start),
        '-t', str(duration),
        '-i', str(input_path),
        '-c:v', 'mpeg4',
        '-qscale:v', '2',  # Use high quality for segments
        '-bf', '0',
        '-g', '9999',
        '-sc_threshold', '0',
        '-pix_fmt', 'yuv420p',
        str(output_path)
    ]
    run_ffmpeg(cmd, f"Extracting segment at {start}s for {duration}s")


def concat_segments(segment_paths, output_path):
    """Concatenate segments using concat demuxer (codec copy)."""
    # Create concat list file
    list_file = output_path.parent / 'concat_list.txt'
    with open(list_file, 'w') as f:
        for seg_path in segment_paths:
            f.write(f"file '{seg_path.absolute()}'\n")

    cmd = [
        'ffmpeg', '-y',
        '-f', 'concat',
        '-safe', '0',
        '-i', str(list_file),
        '-c', 'copy',
        str(output_path)
    ]
    run_ffmpeg(cmd, f"Concatenating {len(segment_paths)} segments")
    list_file.unlink()


def encode_h264(input_path, output_path):
    """Encode final output to H.264 MP4."""
    cmd = [
        'ffmpeg', '-y',
        '-i', str(input_path),
        '-c:v', 'libx264',
        '-crf', '18',
        '-pix_fmt', 'yuv420p',
        str(output_path)
    ]
    run_ffmpeg(cmd, "Encoding to H.264 MP4")


def main():
    parser = argparse.ArgumentParser(
        description='Create heavy P-cascade bloom datamosh transition between two clips',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parser.add_argument('--a', required=True, help='First clip path')
    parser.add_argument('--b', required=True, help='Second clip path')
    parser.add_argument('--fps', type=int, default=30, help='Target framerate')
    parser.add_argument('--width', type=int, default=1280, help='Target width (height auto)')
    parser.add_argument('--q', type=int, default=3, help='MPEG-4 quantizer (lower=higher quality)')
    parser.add_argument('--gop-len', type=int, default=9999, help='GOP length')
    parser.add_argument('--no-iframe-window', type=float, default=1.5,
                       help='Duration (seconds) after join to strip I-frames')
    parser.add_argument('--repeat-boost', type=float, default=0.5,
                       help='Duration (seconds) after join to repeat for smear boost')
    parser.add_argument('--repeat-times', type=int, default=3,
                       help='Number of times to repeat the boost segment')

    args = parser.parse_args()

    # Validate inputs
    print("=== Validating inputs ===")
    validate_inputs(args.a, args.b)

    # Create temp working directory
    temp_dir = Path(tempfile.mkdtemp(prefix='mosh_'))
    print(f"\nWorking directory: {temp_dir}")

    try:
        # Step 1: Normalize inputs
        print("\n=== Step 1: Normalizing inputs ===")
        norm_a = temp_dir / 'A.norm.mp4'
        norm_b = temp_dir / 'B.norm.mp4'
        normalize_clip(args.a, norm_a, args.fps, args.width)
        normalize_clip(args.b, norm_b, args.fps, args.width)

        # Step 2: Concatenate with long GOP
        print("\n=== Step 2: Concatenating with long GOP ===")
        out_longgop = Path.cwd() / 'out_longgop.avi'
        concat_with_longgop(norm_a, norm_b, out_longgop, args.q, args.gop_len)

        # Step 3: Probe join time
        print("\n=== Step 3: Probing join time ===")
        join_t = get_duration(norm_a)
        print(f"JOIN_T = {join_t:.3f}s")

        # Get total duration for validation
        total_duration = get_duration(out_longgop)
        print(f"Total duration = {total_duration:.3f}s")

        # Validate repeat_boost doesn't exceed duration
        if join_t + args.repeat_boost > total_duration:
            print(f"Warning: JOIN_T + repeat_boost ({join_t + args.repeat_boost:.3f}s) "
                  f"exceeds total duration ({total_duration:.3f}s)", file=sys.stderr)
            print(f"Adjusting repeat_boost to {total_duration - join_t:.3f}s", file=sys.stderr)
            args.repeat_boost = total_duration - join_t

        # Step 4: Strip I-frames in window
        print("\n=== Step 4: Stripping I-frames ===")
        mosh_core = Path.cwd() / 'mosh_core.avi'
        strip_iframes_window(out_longgop, mosh_core, join_t, args.no_iframe_window,
                           args.q, args.gop_len)

        # Step 5: Boost smear by repeating segment
        print("\n=== Step 5: Boosting smear ===")

        # Extract segments
        before_join = temp_dir / 'before_join.avi'
        repeat_segment = temp_dir / 'repeat_segment.avi'
        after_repeat = temp_dir / 'after_repeat.avi'

        # Before join: [0, join_t]
        print(f"Extracting before join: [0, {join_t:.3f}]")
        extract_segment(mosh_core, before_join, 0, join_t)

        # Repeat segment: [join_t, join_t + repeat_boost]
        print(f"Extracting repeat segment: [{join_t:.3f}, {join_t + args.repeat_boost:.3f}]")
        extract_segment(mosh_core, repeat_segment, join_t, args.repeat_boost)

        # After repeat: [join_t + repeat_boost, end]
        remaining_duration = get_duration(mosh_core) - (join_t + args.repeat_boost)
        print(f"Extracting after repeat: [{join_t + args.repeat_boost:.3f}, end] "
              f"(duration: {remaining_duration:.3f}s)")
        extract_segment(mosh_core, after_repeat, join_t + args.repeat_boost, remaining_duration)

        # Build concat list: before + repeat*N + after
        segments = [before_join]
        for i in range(args.repeat_times):
            segments.append(repeat_segment)
        segments.append(after_repeat)

        mosh_final_avi = Path.cwd() / 'mosh_final.avi'
        concat_segments(segments, mosh_final_avi)

        # Step 6: Encode to H.264
        print("\n=== Step 6: Encoding final MP4 ===")
        mosh_final_mp4 = Path.cwd() / 'mosh_final.mp4'
        encode_h264(mosh_final_avi, mosh_final_mp4)

        print("\n=== Success! ===")
        print(f"Outputs:")
        print(f"  - {out_longgop}")
        print(f"  - {mosh_core}")
        print(f"  - {mosh_final_avi}")
        print(f"  - {mosh_final_mp4}")

    finally:
        # Clean up temp directory
        print(f"\nCleaning up: {temp_dir}")
        shutil.rmtree(temp_dir)


if __name__ == '__main__':
    main()
