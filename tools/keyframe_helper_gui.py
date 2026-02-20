#!/usr/bin/env python3
"""Standalone GUI helper to find an approximate keyframe/start point for mosh effects."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from keyframe_helper_core import (  # noqa: E402
    build_selection_payload,
    build_selection_info,
    clamp_frame,
    collect_keyframe_times,
    find_nearest_keyframe_frame,
    keyframe_times_to_frames,
    probe_video_info,
)

cv2 = None


LEFT_KEYS = {81, 2424832, ord("a")}
RIGHT_KEYS = {83, 2555904, ord("d")}
BACK_10_KEYS = {82, 2490368, ord("j")}
FWD_10_KEYS = {84, 2621440, ord("l")}
PREV_KEYFRAME_KEYS = {ord("["), ord(","), ord("p")}
NEXT_KEYFRAME_KEYS = {ord("]"), ord("."), ord("n")}
SELECT_KEYS = {13, 10, ord(" "), ord("m")}
SAVE_KEYS = {ord("s"), ord("S")}
QUIT_KEYS = {27, ord("q"), ord("Q")}


def _default_output_path(video_path: str) -> str:
    src = Path(video_path)
    return str(src.with_suffix(".mosh_start.json"))


def _timeline_bounds(frame_width: int, frame_height: int):
    x0 = 24
    x1 = max(x0 + 10, frame_width - 24)
    y0 = max(8, frame_height - 28)
    y1 = frame_height - 12
    return x0, y0, x1, y1


def _frame_from_x(x: int, x0: int, x1: int, frame_count: int) -> int:
    if frame_count <= 1 or x1 <= x0:
        return 0
    ratio = float(x - x0) / float(x1 - x0)
    ratio = max(0.0, min(1.0, ratio))
    return clamp_frame(int(round(ratio * (frame_count - 1))), frame_count)


def _jump_prev_keyframe(current_frame: int, keyframe_frames: list[int]) -> int:
    prev = 0
    for frame in keyframe_frames:
        if frame >= current_frame:
            break
        prev = frame
    return prev


def _jump_next_keyframe(current_frame: int, keyframe_frames: list[int], frame_count: int) -> int:
    for frame in keyframe_frames:
        if frame > current_frame:
            return frame
    return clamp_frame(frame_count - 1, frame_count)


def _draw_overlay(
    frame,
    frame_idx: int,
    frame_count: int,
    fps: float,
    keyframe_frames: list[int],
    selected_frame: int | None,
):
    cv2_mod = _require_cv2()
    h, w = frame.shape[:2]
    x0, y0, x1, y1 = _timeline_bounds(w, h)

    disp = frame.copy()
    cv2_mod.rectangle(disp, (x0, y0), (x1, y1), (30, 30, 30), -1)
    cv2_mod.rectangle(disp, (x0, y0), (x1, y1), (180, 180, 180), 1)

    if frame_count > 1:
        draw_step = 1
        if len(keyframe_frames) > 1200:
            draw_step = max(1, len(keyframe_frames) // 1200)
        for keyframe in keyframe_frames[::draw_step]:
            kx = x0 + int(round((float(keyframe) / float(frame_count - 1)) * (x1 - x0)))
            cv2_mod.line(disp, (kx, y0), (kx, y1), (90, 90, 90), 1)

        cur_x = x0 + int(round((float(frame_idx) / float(frame_count - 1)) * (x1 - x0)))
        cv2_mod.line(disp, (cur_x, y0 - 8), (cur_x, y1 + 4), (0, 255, 0), 2)
        if selected_frame is not None:
            sel_x = x0 + int(round((float(selected_frame) / float(frame_count - 1)) * (x1 - x0)))
            cv2_mod.line(disp, (sel_x, y0 - 8), (sel_x, y1 + 4), (0, 80, 255), 2)

    time_sec = frame_idx / fps if fps > 0 else 0.0
    nearest = find_nearest_keyframe_frame(frame_idx, keyframe_frames)
    delta = frame_idx - nearest
    line1 = f"frame {frame_idx}/{max(0, frame_count-1)}  time {time_sec:.3f}s  nearest keyframe {nearest} (delta {delta:+d})"
    line2 = "Controls: left/right=1  up/down=10  [ ]=prev/next keyframe  space=select start  s=save json  q=quit"
    line3 = (
        "Selected start: none"
        if selected_frame is None
        else f"Selected start frame {selected_frame} ({selected_frame / fps if fps > 0 else 0.0:.3f}s)"
    )
    cv2_mod.putText(disp, line1, (16, 26), cv2_mod.FONT_HERSHEY_SIMPLEX, 0.58, (255, 255, 255), 1, cv2_mod.LINE_AA)
    cv2_mod.putText(disp, line2, (16, 50), cv2_mod.FONT_HERSHEY_SIMPLEX, 0.50, (230, 230, 230), 1, cv2_mod.LINE_AA)
    cv2_mod.putText(disp, line3, (16, 74), cv2_mod.FONT_HERSHEY_SIMPLEX, 0.54, (0, 220, 255), 1, cv2_mod.LINE_AA)
    return disp, (x0, y0, x1, y1)


def _save_selection_json(
    output_path: str,
    payload: dict,
):
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    print(f"[OK] Wrote selection: {output_path}")
    print(f"[SUGGEST] Bloom:  {payload['suggested_commands']['bloom_manual_pivot']}")
    print(f"[SUGGEST] Classic:{payload['suggested_commands']['classic_drop_window']}")


def _require_cv2():
    global cv2
    if cv2 is not None:
        return cv2
    try:
        import cv2 as cv2_mod
    except ImportError as exc:
        raise RuntimeError(
            "OpenCV is required for the GUI helper. Install with: pip install opencv-python"
        ) from exc
    cv2 = cv2_mod
    return cv2


def run_gui(video_path: str, output_path: str, repeat_count: int, kill_ratio: float):
    cv2_mod = _require_cv2()
    if not os.path.exists(video_path):
        raise FileNotFoundError(video_path)

    video_info = probe_video_info(video_path)
    keyframe_times = collect_keyframe_times(video_path)
    keyframe_frames = keyframe_times_to_frames(keyframe_times, video_info)

    cap = cv2_mod.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Failed to open video: {video_path}")

    window_name = "d4t4m0sh Keyframe Helper"
    state = {
        "frame_idx": clamp_frame(0, video_info.frame_count),
        "selected_frame": None,
        "timeline": (0, 0, 0, 0),
    }

    def _on_mouse(event, x, y, _flags, _param):
        if event != cv2_mod.EVENT_LBUTTONDOWN:
            return
        x0, y0, x1, y1 = state["timeline"]
        if x0 <= x <= x1 and y0 <= y <= y1:
            state["frame_idx"] = _frame_from_x(x, x0, x1, video_info.frame_count)

    cv2_mod.namedWindow(window_name, cv2_mod.WINDOW_NORMAL)
    cv2_mod.setMouseCallback(window_name, _on_mouse)

    try:
        while True:
            frame_idx = clamp_frame(state["frame_idx"], video_info.frame_count)
            cap.set(cv2_mod.CAP_PROP_POS_FRAMES, float(frame_idx))
            ok, frame = cap.read()
            if not ok or frame is None:
                state["frame_idx"] = clamp_frame(frame_idx - 1, video_info.frame_count)
                continue

            canvas, bounds = _draw_overlay(
                frame,
                frame_idx=frame_idx,
                frame_count=video_info.frame_count,
                fps=video_info.fps,
                keyframe_frames=keyframe_frames,
                selected_frame=state["selected_frame"],
            )
            state["timeline"] = bounds
            cv2_mod.imshow(window_name, canvas)
            key = cv2_mod.waitKeyEx(0)

            if key in QUIT_KEYS:
                break
            if key in LEFT_KEYS:
                state["frame_idx"] = clamp_frame(frame_idx - 1, video_info.frame_count)
                continue
            if key in RIGHT_KEYS:
                state["frame_idx"] = clamp_frame(frame_idx + 1, video_info.frame_count)
                continue
            if key in BACK_10_KEYS:
                state["frame_idx"] = clamp_frame(frame_idx - 10, video_info.frame_count)
                continue
            if key in FWD_10_KEYS:
                state["frame_idx"] = clamp_frame(frame_idx + 10, video_info.frame_count)
                continue
            if key in PREV_KEYFRAME_KEYS:
                state["frame_idx"] = _jump_prev_keyframe(frame_idx, keyframe_frames)
                continue
            if key in NEXT_KEYFRAME_KEYS:
                state["frame_idx"] = _jump_next_keyframe(frame_idx, keyframe_frames, video_info.frame_count)
                continue
            if key in SELECT_KEYS:
                state["selected_frame"] = frame_idx
                selected = build_selection_info(video_info, frame_idx, keyframe_frames)
                print(
                    f"[SELECTED] frame={selected.selected_frame} "
                    f"time={selected.selected_time:.3f}s "
                    f"nearest_keyframe={selected.nearest_keyframe_frame}"
                )
                continue
            if key in SAVE_KEYS:
                pick = frame_idx if state["selected_frame"] is None else int(state["selected_frame"])
                payload = build_selection_payload(
                    video_info=video_info,
                    selected_frame=pick,
                    keyframe_frames=keyframe_frames,
                    repeat_count=repeat_count,
                    kill_ratio=kill_ratio,
                )
                _save_selection_json(output_path, payload)
                continue
    finally:
        cap.release()
        cv2_mod.destroyAllWindows()


def main():
    parser = argparse.ArgumentParser(
        description="Standalone GUI helper to find an approximate keyframe/start frame for datamosh effects."
    )
    parser.add_argument("--in", dest="input_path", required=True, help="Input video path")
    parser.add_argument(
        "--out-json",
        dest="out_json",
        default=None,
        help="Where to save selected keyframe info JSON (default: <input>.mosh_start.json)",
    )
    parser.add_argument(
        "--repeat-count",
        type=int,
        default=12,
        help="Suggested repeat count to include in generated bloom command",
    )
    parser.add_argument(
        "--kill-ratio",
        type=float,
        default=1.0,
        help="Suggested kill ratio to include in generated bloom command",
    )
    args = parser.parse_args()

    output_path = args.out_json or _default_output_path(args.input_path)
    run_gui(
        video_path=args.input_path,
        output_path=output_path,
        repeat_count=args.repeat_count,
        kill_ratio=args.kill_ratio,
    )


if __name__ == "__main__":
    main()
