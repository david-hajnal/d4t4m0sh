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
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import List, Sequence

try:
    import av
except ImportError:
    av = None


def _require_av():
    if av is None:
        raise RuntimeError("PyAV is not installed. Install with: pip install av")


@dataclass(frozen=True)
class FrameChunk:
    payload: bytes
    frame_size: int
    pts: int | None
    dts: int | None


def check_codec(input_path, verbose=False):
    """Check if input uses MPEG-4 ASP / Xvid codec."""
    _require_av()
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


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp_pivot_frame(pivot_frame, frame_count):
    if frame_count <= 0:
        return 0
    pivot = _safe_int(pivot_frame, 0)
    if pivot < 0:
        return 0
    if pivot >= frame_count:
        return frame_count - 1
    return pivot


def _sanitize_repeat_count(repeat_count):
    return max(0, _safe_int(repeat_count, 0))


def _filter_frame_chunks(frame_chunks: Sequence[FrameChunk], kill_ratio: float, required_initial_frames: int = 1):
    if not frame_chunks:
        return [], 0

    keep_head = max(1, _safe_int(required_initial_frames, 1))
    ratio = max(0.0, _safe_float(kill_ratio, 1.0))
    max_frame_size = max(chunk.frame_size for chunk in frame_chunks)
    threshold = max_frame_size * ratio

    filtered = []
    for idx, chunk in enumerate(frame_chunks):
        if idx < keep_head or chunk.frame_size <= threshold:
            filtered.append(chunk)

    if not filtered:
        filtered = list(frame_chunks[:keep_head])

    return filtered, max_frame_size


def build_bloom_sequence(frames: Sequence, pivot_frame, repeat_count):
    """
    Build bloom ordering exactly as:
      prefix = frames[:pivot]
      burst = [frames[pivot]] * repeat_count
      suffix = frames[pivot:]
      result = prefix + burst + suffix
    """
    if not frames:
        return [], 0, _sanitize_repeat_count(repeat_count)

    safe_pivot = _clamp_pivot_frame(pivot_frame, len(frames))
    safe_repeat = _sanitize_repeat_count(repeat_count)
    prefix = list(frames[:safe_pivot])
    burst = [frames[safe_pivot]] * safe_repeat
    suffix = list(frames[safe_pivot:])
    return prefix + burst + suffix, safe_pivot, safe_repeat


def _collect_video_frame_chunks(container, video_stream) -> List[FrameChunk]:
    chunks: List[FrameChunk] = []
    for packet in container.demux(video_stream):
        if packet.dts is None and packet.pts is None:
            continue
        payload = bytes(packet)
        if not payload:
            continue
        chunks.append(
            FrameChunk(
                payload=payload,
                frame_size=len(payload),
                pts=packet.pts,
                dts=packet.dts,
            )
        )
    return chunks


def _estimate_packet_ticks(frame_chunks: Sequence[FrameChunk], time_base, fallback_fps):
    deltas = []
    dts_values = [c.dts for c in frame_chunks if c.dts is not None]
    if len(dts_values) > 1:
        for left, right in zip(dts_values, dts_values[1:]):
            delta = right - left
            if delta > 0:
                deltas.append(delta)

    if not deltas:
        pts_values = [c.pts for c in frame_chunks if c.pts is not None]
        for left, right in zip(pts_values, pts_values[1:]):
            delta = right - left
            if delta > 0:
                deltas.append(delta)

    if deltas:
        return max(1, min(deltas))

    fps = fallback_fps or 24
    try:
        return max(1, int(round((Fraction(1, 1) / fps) / time_base)))
    except Exception:
        return 1


def _packet_from_payload(payload: bytes):
    pkt = av.Packet(len(payload))
    if payload:
        memoryview(pkt)[:] = payload
    return pkt


def _add_stream_from_template(out_container, src_stream):
    src_codec = getattr(src_stream, "codec_context", None)
    src_codec_name = (
        getattr(src_codec, "name", None)
        or getattr(getattr(src_stream, "codec", None), "name", None)
        or "mpeg4"
    )
    try:
        out_stream = out_container.add_stream(template=src_stream)
    except Exception:
        add_kwargs = {}
        src_rate = getattr(src_stream, "average_rate", None)
        if src_rate:
            add_kwargs["rate"] = src_rate
        try:
            out_stream = out_container.add_stream(src_codec_name, **add_kwargs)
        except TypeError:
            out_stream = out_container.add_stream(src_codec_name)

        if src_stream.type == "video" and src_codec is not None:
            width = getattr(src_codec, "width", None)
            height = getattr(src_codec, "height", None)
            pix_fmt = getattr(src_codec, "pix_fmt", None)
            if width:
                try:
                    out_stream.width = width
                except Exception:
                    pass
            if height:
                try:
                    out_stream.height = height
                except Exception:
                    pass
            if pix_fmt:
                try:
                    out_stream.pix_fmt = pix_fmt
                except Exception:
                    pass

        try:
            out_stream.time_base = src_stream.time_base
        except Exception:
            pass
        try:
            if src_codec is not None and src_codec.extradata:
                out_stream.codec_context.extradata = src_codec.extradata
        except Exception:
            pass

    try:
        out_stream.codec_tag = src_stream.codec_tag
    except Exception:
        pass
    return out_stream


def _mux_video_chunks(out_container, out_stream, chunks: Sequence[FrameChunk], time_base, ticks):
    if not chunks:
        return

    time_base_to_use = out_stream.time_base or time_base
    frame_ticks = max(1, _safe_int(ticks, 1))
    base_pts = 0
    base_dts = 0

    for idx, chunk in enumerate(chunks):
        pkt = _packet_from_payload(chunk.payload)
        pkt.pts = base_pts + (idx * frame_ticks)
        pkt.dts = base_dts + (idx * frame_ticks)
        pkt.duration = frame_ticks
        pkt.time_base = time_base_to_use
        pkt.stream = out_stream
        out_container.mux(pkt)


def _mux_audio_packets(in_container, in_audio_stream, out_container, out_audio_stream):
    try:
        in_container.seek(0)
    except Exception:
        pass
    for packet in in_container.demux(in_audio_stream):
        if packet.dts is None and packet.pts is None:
            continue
        try:
            packet.rescale_ts(in_audio_stream.time_base, out_audio_stream.time_base)
        except Exception:
            pass
        packet.stream = out_audio_stream
        out_container.mux(packet)


def _run_process(cmd):
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr}")
    return proc


def _finalize_bloom_output(video_path, output_path, audio_source=None):
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
    ]

    if audio_source:
        cmd.extend(["-i", str(audio_source), "-map", "0:v:0", "-map", "1:a?", "-c:v", "copy"])
        ext = os.path.splitext(str(output_path))[1].lower()
        if ext in {".mp4", ".mov", ".m4v"}:
            cmd.extend(["-c:a", "aac", "-b:a", "192k"])
        else:
            cmd.extend(["-c:a", "copy"])
    else:
        cmd.extend(["-map", "0:v:0", "-c:v", "copy", "-an"])

    cmd.append(str(output_path))
    _run_process(cmd)


def bloom_mosh(input_path, output_path, pivot_frame, repeat_count, kill_ratio=1.0, keep_audio=False):
    """
    Bloom datamosh by duplicating one packet chunk many times.
    Operates in compressed domain: demux packet chunks, reorder, remux.
    """
    _require_av()

    input_path = str(input_path)
    output_path = str(output_path)
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    output_ext = os.path.splitext(output_path)[1].lower()
    needs_finalize_mux = output_ext != ".avi" or bool(keep_audio)
    staging_output = output_path
    staging_temp = None
    if needs_finalize_mux:
        staging_temp = tempfile.NamedTemporaryFile(suffix=".avi", delete=False)
        staging_temp.close()
        staging_output = staging_temp.name

    in_container = av.open(input_path)
    out_container = None
    write_complete = False
    try:
        video_stream = next((s for s in in_container.streams if s.type == "video"), None)
        if video_stream is None:
            raise RuntimeError("No video stream found")

        frame_chunks = _collect_video_frame_chunks(in_container, video_stream)
        if not frame_chunks:
            raise RuntimeError("No video packets found")

        filtered_frames, max_frame_size = _filter_frame_chunks(frame_chunks, kill_ratio, required_initial_frames=1)
        ordered_frames, safe_pivot, safe_repeat = build_bloom_sequence(
            filtered_frames, pivot_frame, repeat_count
        )

        frame_ticks = _estimate_packet_ticks(filtered_frames, video_stream.time_base, video_stream.average_rate)

        out_container = av.open(staging_output, mode="w")
        out_video_stream = _add_stream_from_template(out_container, video_stream)
        _mux_video_chunks(out_container, out_video_stream, ordered_frames, video_stream.time_base, frame_ticks)
        write_complete = True
    finally:
        if out_container is not None:
            out_container.close()
        in_container.close()
        if staging_temp is not None and os.path.exists(staging_output):
            try:
                if write_complete:
                    _finalize_bloom_output(
                        staging_output,
                        output_path,
                        audio_source=input_path if keep_audio else None,
                    )
            finally:
                os.unlink(staging_output)

    return {
        "input_frames": len(frame_chunks),
        "filtered_frames": len(filtered_frames),
        "output_frames": len(ordered_frames),
        "inserted_frames": len(ordered_frames) - len(filtered_frames),
        "pivot_frame": safe_pivot,
        "repeat_count": safe_repeat,
        "max_frame_size": max_frame_size,
        "keep_audio": bool(keep_audio),
    }


def get_video_duration(container, video_stream):
    """Get video duration in seconds."""
    _require_av()
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

    _require_av()

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
  # Bloom burst effect (packet-level frame chunk duplication)
  python aviglitch_mosh.py --in input.avi --out bloom.avi \\
    --effect bloom --pivot-frame 120 --repeat-count 24 --kill-ratio 0.8

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
    parser.add_argument(
        '--effect',
        type=str,
        default='classic',
        choices=['classic', 'bloom'],
        help='classic: drop/dup behavior, bloom: pivot-frame burst insertion'
    )

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

    # Bloom effect options
    parser.add_argument('--pivot-frame', type=int, default=0,
                        help='[effect=bloom] Pivot frame chunk index to duplicate')
    parser.add_argument('--repeat-count', type=int, default=12,
                        help='[effect=bloom] Number of duplicate chunks to insert')
    parser.add_argument('--kill-ratio', type=float, default=1.0,
                        help='[effect=bloom] Keep chunks where size <= max_size * kill_ratio')
    parser.add_argument('--keep-audio', action='store_true',
                        help='[effect=bloom] Copy audio stream to output')

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

    if args.effect == "classic":
        # Validate at least one classic operation specified
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
        if args.effect == "bloom":
            stats = bloom_mosh(
                work_input,
                output_path,
                pivot_frame=args.pivot_frame,
                repeat_count=args.repeat_count,
                kill_ratio=args.kill_ratio,
                keep_audio=args.keep_audio,
            )
            if args.verbose:
                print("\n=== Bloom Summary ===")
                print(f"Input packets: {stats['input_frames']}")
                print(f"Filtered packets: {stats['filtered_frames']}")
                print(f"Output packets: {stats['output_frames']}")
                print(f"Inserted packets: {stats['inserted_frames']}")
                print(f"Pivot frame: {stats['pivot_frame']}")
                print(f"Repeat count: {stats['repeat_count']}")
                print(f"Max frame size: {stats['max_frame_size']}")
                print(f"Keep audio: {stats['keep_audio']}")
            print(f"✓ Output: {output_path}")
        else:
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
