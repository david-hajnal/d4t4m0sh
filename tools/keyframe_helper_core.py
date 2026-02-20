#!/usr/bin/env python3
"""Core utilities for standalone keyframe-start selection helpers."""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Sequence


@dataclass(frozen=True)
class VideoInfo:
    path: str
    fps: float
    frame_count: int
    duration: float


@dataclass(frozen=True)
class SelectionInfo:
    selected_frame: int
    selected_time: float
    nearest_keyframe_frame: int
    nearest_keyframe_time: float
    frame_delta_to_keyframe: int
    time_delta_to_keyframe: float


def _run_ffprobe_json(cmd: Sequence[str]) -> dict:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr}")
    return json.loads(proc.stdout or "{}")


def _parse_fps(rate: str | None) -> float:
    if not rate:
        return 30.0
    try:
        n_str, d_str = str(rate).split("/", 1)
        n = float(n_str)
        d = float(d_str)
        if d == 0:
            return 30.0
        fps = n / d
        return fps if fps > 0 else 30.0
    except Exception:
        return 30.0


def clamp_frame(frame_idx: int, frame_count: int) -> int:
    if frame_count <= 0:
        return 0
    if frame_idx < 0:
        return 0
    if frame_idx >= frame_count:
        return frame_count - 1
    return int(frame_idx)


def frame_to_time(frame_idx: int, fps: float) -> float:
    fps_safe = fps if fps > 0 else 30.0
    return float(frame_idx) / float(fps_safe)


def probe_video_info(video_path: str) -> VideoInfo:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=avg_frame_rate,nb_frames,duration:format=duration",
        "-of",
        "json",
        str(video_path),
    ]
    payload = _run_ffprobe_json(cmd)
    streams = payload.get("streams") or []
    if not streams:
        raise RuntimeError(f"No video stream found: {video_path}")
    stream = streams[0]

    fps = _parse_fps(stream.get("avg_frame_rate"))

    duration = 0.0
    for key in ("duration",):
        raw = stream.get(key)
        if raw not in (None, ""):
            try:
                duration = float(raw)
                break
            except Exception:
                pass
    if duration <= 0:
        raw_fmt = (payload.get("format") or {}).get("duration")
        if raw_fmt not in (None, ""):
            try:
                duration = float(raw_fmt)
            except Exception:
                duration = 0.0

    frame_count = 0
    raw_nb = stream.get("nb_frames")
    if raw_nb not in (None, "", "N/A"):
        try:
            frame_count = int(raw_nb)
        except Exception:
            frame_count = 0
    if frame_count <= 0 and duration > 0:
        frame_count = max(1, int(round(duration * fps)))
    if frame_count <= 0:
        frame_count = 1
    if duration <= 0:
        duration = float(frame_count) / float(fps if fps > 0 else 30.0)

    return VideoInfo(path=str(video_path), fps=float(fps), frame_count=int(frame_count), duration=float(duration))


def collect_keyframe_times(video_path: str) -> List[float]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-skip_frame",
        "nokey",
        "-show_frames",
        "-show_entries",
        "frame=key_frame,best_effort_timestamp_time,pkt_pts_time",
        "-of",
        "json",
        str(video_path),
    ]
    payload = _run_ffprobe_json(cmd)
    frames = payload.get("frames") or []

    values: List[float] = []
    for frame in frames:
        if int(frame.get("key_frame", 0)) != 1:
            continue
        ts_raw = frame.get("best_effort_timestamp_time")
        if ts_raw in (None, ""):
            ts_raw = frame.get("pkt_pts_time")
        if ts_raw in (None, ""):
            continue
        try:
            values.append(float(ts_raw))
        except Exception:
            continue

    if not values:
        return [0.0]
    uniq = sorted(set(values))
    return uniq


def keyframe_times_to_frames(keyframe_times: Sequence[float], video_info: VideoInfo) -> List[int]:
    out: List[int] = []
    for ts in keyframe_times:
        try:
            idx = int(round(float(ts) * video_info.fps))
        except Exception:
            continue
        out.append(clamp_frame(idx, video_info.frame_count))
    if not out:
        return [0]
    return sorted(set(out))


def find_nearest_keyframe_frame(frame_idx: int, keyframe_frames: Sequence[int]) -> int:
    if not keyframe_frames:
        return int(frame_idx)
    best = keyframe_frames[0]
    best_delta = abs(best - frame_idx)
    for kf in keyframe_frames[1:]:
        delta = abs(kf - frame_idx)
        if delta < best_delta:
            best = kf
            best_delta = delta
    return int(best)


def build_selection_info(video_info: VideoInfo, selected_frame: int, keyframe_frames: Sequence[int]) -> SelectionInfo:
    safe_frame = clamp_frame(int(selected_frame), video_info.frame_count)
    selected_time = frame_to_time(safe_frame, video_info.fps)

    nearest_frame = find_nearest_keyframe_frame(safe_frame, keyframe_frames)
    nearest_time = frame_to_time(nearest_frame, video_info.fps)

    frame_delta = safe_frame - nearest_frame
    time_delta = selected_time - nearest_time
    return SelectionInfo(
        selected_frame=safe_frame,
        selected_time=selected_time,
        nearest_keyframe_frame=nearest_frame,
        nearest_keyframe_time=nearest_time,
        frame_delta_to_keyframe=frame_delta,
        time_delta_to_keyframe=time_delta,
    )


def build_command_suggestions(
    video_info: VideoInfo,
    selection: SelectionInfo,
    repeat_count: int = 12,
    kill_ratio: float = 1.0,
) -> dict:
    input_q = shlex.quote(video_info.path)
    stem = Path(video_info.path).stem
    out_q = shlex.quote(f"{stem}.moshed.mp4")

    repeat = max(0, int(repeat_count))
    kill = max(0.0, float(kill_ratio))
    start_t = max(0.0, selection.selected_time)
    end_t = min(video_info.duration, start_t + 2.0)
    if end_t <= start_t:
        end_t = start_t + 0.5

    bloom = (
        "python3 aviglitch_mosh.py "
        f"--in {input_q} --out {out_q} --effect bloom "
        f"--pivot-frame {selection.selected_frame} --repeat-count {repeat} --kill-ratio {kill:.3f}"
    )
    classic = (
        "python3 aviglitch_mosh.py "
        f"--in {input_q} --out {out_q} --effect classic "
        f"--drop-start {start_t:.3f} --drop-end {end_t:.3f}"
    )
    return {
        "bloom_manual_pivot": bloom,
        "classic_drop_window": classic,
    }


def build_selection_payload(
    video_info: VideoInfo,
    selected_frame: int,
    keyframe_frames: Sequence[int],
    repeat_count: int = 12,
    kill_ratio: float = 1.0,
) -> dict:
    selection = build_selection_info(video_info, selected_frame, keyframe_frames)
    return {
        "video": asdict(video_info),
        "selection": asdict(selection),
        "suggested_commands": build_command_suggestions(
            video_info,
            selection,
            repeat_count=repeat_count,
            kill_ratio=kill_ratio,
        ),
    }
