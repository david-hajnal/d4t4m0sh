#!/usr/bin/env python3
"""
mosh_zoom_oneclip - Single-clip artificial zoom + datamosh

Creates a "melting zoom" effect by freezing a frame, generating an artificial zoom,
and forcing P-cascade across the zoom with no new I-frames.
"""

import argparse
import subprocess
import sys
import os
import json
import tempfile
import shutil
from pathlib import Path


def run_ffmpeg(cmd, description, verbose=False):
    """Execute FFmpeg command with error handling."""
    print(f"\n[{description}]")
    if verbose:
        print(f"Command: {' '.join(cmd)}")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error: {description} failed", file=sys.stderr)
        print(f"Command: {' '.join(cmd)}", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)

    if verbose and result.stderr:
        print(result.stderr)

    return result


def get_video_info(video_path):
    """Get video duration and dimensions using ffprobe."""
    cmd = [
        'ffprobe',
        '-v', 'error',
        '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height:format=duration',
        '-of', 'json',
        str(video_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error: Failed to probe {video_path}", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)

    data = json.loads(result.stdout)
    stream = data['streams'][0]
    width = int(stream['width'])
    height = int(stream['height'])
    duration = float(data['format']['duration'])
    return width, height, duration


def timestamp_to_seconds(timestamp):
    """Convert HH:MM:SS.mmm timestamp to seconds."""
    if isinstance(timestamp, (int, float)):
        return float(timestamp)

    parts = timestamp.split(':')
    if len(parts) == 1:
        return float(parts[0])
    elif len(parts) == 2:
        return float(parts[0]) * 60 + float(parts[1])
    elif len(parts) == 3:
        return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    else:
        raise ValueError(f"Invalid timestamp format: {timestamp}")


def validate_inputs(input_path, t_seconds, duration):
    """Validate that input file exists and timestamp is valid."""
    if not Path(input_path).exists():
        print(f"Error: Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    if t_seconds < 0 or t_seconds >= duration:
        print(f"Error: Timestamp {t_seconds:.3f}s is outside video duration [0, {duration:.3f}s]", file=sys.stderr)
        sys.exit(1)


def normalize_clip(input_path, output_path, fps, width, verbose=False):
    """Normalize clip to deterministic params (no audio)."""
    cmd = [
        'ffmpeg', '-y', '-i', str(input_path),
        '-vf', f'fps={fps},scale={width}:-2,format=yuv420p',
        '-an',
        str(output_path)
    ]
    run_ffmpeg(cmd, f"Normalizing {Path(input_path).name}", verbose)


def extract_pivot_frame(norm_path, timestamp_sec, output_path, verbose=False):
    """Extract single frame at timestamp T."""
    cmd = [
        'ffmpeg', '-y',
        '-ss', str(timestamp_sec),
        '-i', str(norm_path),
        '-frames:v', '1',
        str(output_path)
    ]
    run_ffmpeg(cmd, f"Extracting pivot frame at {timestamp_sec:.3f}s", verbose)


def generate_zoom(pivot_png, output_path, zoom_dur, zoom_direction, fps, width, height, verbose=False):
    """Generate artificial zoom ramp from still frame."""
    # Calculate zoom step to reach ~1.5-2.0x over duration
    total_frames = int(zoom_dur * fps)
    if zoom_direction == 'out':
        # Zoom out (image gets bigger)
        zoom_step = 0.02
        zoom_expr = f"min(pzoom+{zoom_step},2.0)"
    else:  # 'in'
        # Zoom in (image gets smaller / pull out)
        zoom_expr = f"max(2.0-on/{total_frames},1.0)"

    vf = f"zoompan=z='{zoom_expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={width}x{height},fps={fps},format=yuv420p"

    cmd = [
        'ffmpeg', '-y',
        '-loop', '1',
        '-t', str(zoom_dur),
        '-i', str(pivot_png),
        '-vf', vf,
        '-an',
        str(output_path)
    ]
    run_ffmpeg(cmd, f"Generating {zoom_direction} zoom ({zoom_dur}s)", verbose)


def split_at_timestamp(norm_path, timestamp_sec, before_path, after_path, verbose=False):
    """Split original clip at timestamp T."""
    # Before: [0, T]
    cmd_before = [
        'ffmpeg', '-y',
        '-i', str(norm_path),
        '-t', str(timestamp_sec),
        '-c', 'copy',
        str(before_path)
    ]
    run_ffmpeg(cmd_before, f"Extracting before segment [0, {timestamp_sec:.3f}s]", verbose)

    # After: [T, end]
    cmd_after = [
        'ffmpeg', '-y',
        '-ss', str(timestamp_sec),
        '-i', str(norm_path),
        '-c', 'copy',
        str(after_path)
    ]
    run_ffmpeg(cmd_after, f"Extracting after segment [{timestamp_sec:.3f}s, end]", verbose)


def concat_with_longgop(before_path, zoom_path, after_path, output_path, q, verbose=False):
    """Concatenate A_before + zoom + A_after with MPEG-4 ASP, no B-frames, huge GOP."""
    cmd = [
        'ffmpeg', '-y',
        '-i', str(before_path),
        '-i', str(zoom_path),
        '-i', str(after_path),
        '-filter_complex', '[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]',
        '-map', '[v]',
        '-c:v', 'mpeg4',
        '-qscale:v', str(q),
        '-bf', '0',
        '-g', '9999',
        '-sc_threshold', '0',
        '-pix_fmt', 'yuv420p',
        str(output_path)
    ]
    run_ffmpeg(cmd, "Concatenating with long GOP", verbose)


def strip_iframes_window(input_path, output_path, win_start, win_end, q, verbose=False):
    """Force P-cascade by stripping I-frames in window [win_start, win_end]."""
    select_expr = f"select='not(between(t,{win_start},{win_end})*eq(pict_type,I))',setpts=N/FRAME_RATE/TB"

    cmd = [
        'ffmpeg', '-y',
        '-i', str(input_path),
        '-vf', select_expr,
        '-c:v', 'mpeg4',
        '-qscale:v', str(q),
        '-bf', '0',
        '-g', '9999',
        '-sc_threshold', '0',
        '-pix_fmt', 'yuv420p',
        str(output_path)
    ]
    run_ffmpeg(cmd, f"Stripping I-frames in window [{win_start:.3f}s, {win_end:.3f}s]", verbose)


def encode_h264(input_path, output_path, crf, verbose=False):
    """Encode final output to H.264 MP4."""
    cmd = [
        'ffmpeg', '-y',
        '-i', str(input_path),
        '-c:v', 'libx264',
        '-crf', str(crf),
        '-pix_fmt', 'yuv420p',
        str(output_path)
    ]
    run_ffmpeg(cmd, "Encoding to H.264 MP4", verbose)


def main():
    parser = argparse.ArgumentParser(
        description='Single-clip artificial zoom + datamosh (melting zoom effect)',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parser.add_argument('--in', dest='input', required=True, help='Input video path')
    parser.add_argument('--out', required=True, help='Output MP4 path')
    parser.add_argument('--fps', type=int, default=30, help='Target framerate')
    parser.add_argument('--width', type=int, default=1280, help='Target width')
    parser.add_argument('--q', type=int, default=3, help='MPEG-4 ASP quantizer for intermediates')
    parser.add_argument('--t', default='00:00:05.000', help='Mosh start timestamp (HH:MM:SS.mmm or seconds)')
    parser.add_argument('--zoom-dur', type=float, default=1.0, help='Zoom duration (seconds)')
    parser.add_argument('--tail', type=float, default=1.0, help='Seconds of P-only after zoom')
    parser.add_argument('--zoom-direction', choices=['out', 'in'], default='out',
                       help='Zoom direction: out=push in (bigger), in=pull out (smaller)')
    parser.add_argument('--deliver-crf', type=int, default=18, help='x264 CRF for final MP4')
    parser.add_argument('--workdir', help='Work directory (optional, else temp dir)')
    parser.add_argument('--keep-intermediates', action='store_true',
                       help='Keep intermediate files')
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output')

    args = parser.parse_args()

    # Validate inputs
    print("=== Validating inputs ===")
    if not Path(args.input).exists():
        print(f"Error: Input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    # Get video info
    _, _, duration = get_video_info(args.input)
    print(f"Input duration: {duration:.3f}s")

    # Parse timestamp
    t_seconds = timestamp_to_seconds(args.t)
    print(f"Mosh start time: {t_seconds:.3f}s")

    validate_inputs(args.input, t_seconds, duration)

    # Setup work directory
    if args.workdir:
        work_dir = Path(args.workdir)
        work_dir.mkdir(parents=True, exist_ok=True)
        cleanup = False
    else:
        work_dir = Path(tempfile.mkdtemp(prefix='mosh_zoom_'))
        cleanup = not args.keep_intermediates

    print(f"\nWork directory: {work_dir}")

    try:
        # Step 1: Normalize
        print("\n=== Step 1: Normalizing input ===")
        norm_path = work_dir / 'norm.mp4'
        normalize_clip(args.input, norm_path, args.fps, args.width, args.verbose)

        # Get normalized dimensions
        norm_width, norm_height, _ = get_video_info(norm_path)
        print(f"Normalized size: {norm_width}x{norm_height}")

        # Step 2: Extract pivot frame
        print("\n=== Step 2: Extracting pivot frame ===")
        pivot_png = work_dir / 'pivot.png'
        extract_pivot_frame(norm_path, t_seconds, pivot_png, args.verbose)

        # Step 3: Generate zoom
        print("\n=== Step 3: Generating zoom ramp ===")
        zoom_path = work_dir / 'zoom.mp4'
        generate_zoom(pivot_png, zoom_path, args.zoom_dur, args.zoom_direction,
                     args.fps, norm_width, norm_height, args.verbose)

        # Step 4: Split original
        print("\n=== Step 4: Splitting original at T ===")
        before_path = work_dir / 'A_before.mp4'
        after_path = work_dir / 'A_after.mp4'
        split_at_timestamp(norm_path, t_seconds, before_path, after_path, args.verbose)

        # Step 5: Concatenate with long GOP
        print("\n=== Step 5: Concatenating with long GOP ===")
        longgop_path = work_dir / 'longgop.avi'
        concat_with_longgop(before_path, zoom_path, after_path, longgop_path, args.q, args.verbose)

        # Step 6: Force P-cascade
        print("\n=== Step 6: Forcing P-cascade ===")
        win_start = t_seconds
        win_end = t_seconds + args.zoom_dur + args.tail
        print(f"I-frame strip window: [{win_start:.3f}s, {win_end:.3f}s]")

        mosh_zoom_path = work_dir / 'mosh_zoom.avi'
        strip_iframes_window(longgop_path, mosh_zoom_path, win_start, win_end, args.q, args.verbose)

        # Step 7: Final deliverable
        print("\n=== Step 7: Encoding final MP4 ===")
        output_path = Path(args.out)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        encode_h264(mosh_zoom_path, output_path, args.deliver_crf, args.verbose)

        print("\n=== Success! ===")
        print(f"Output: {output_path}")

        if args.keep_intermediates or args.workdir:
            print(f"\nIntermediates saved in: {work_dir}")
            print("  - norm.mp4")
            print("  - pivot.png")
            print("  - zoom.mp4")
            print("  - A_before.mp4")
            print("  - A_after.mp4")
            print("  - longgop.avi")
            print("  - mosh_zoom.avi")

    finally:
        # Clean up if requested
        if cleanup:
            print(f"\nCleaning up: {work_dir}")
            shutil.rmtree(work_dir)


if __name__ == '__main__':
    main()
