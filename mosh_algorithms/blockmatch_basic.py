import cv2
import numpy as np
from tqdm import tqdm

def _video_props(cap):
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    if fps <= 1e-3:
        fps = 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    return int(w), int(h), float(fps)

def _block_match(prev_gray, cur_gray, B=16, R=8):
    H, W = cur_gray.shape
    mv = np.zeros((H // B, W // B, 2), dtype=np.int16)
    for by in range(0, H, B):
        if by + B > H: break
        for bx in range(0, W, B):
            if bx + B > W: break
            block = cur_gray[by:by+B, bx:bx+B]
            best_sad = 1e18
            best_dx = 0
            best_dy = 0
            y0min = max(0, by - R); y0max = min(H - B, by + R)
            x0min = max(0, bx - R); x0max = min(W - B, bx + R)
            for y0 in range(y0min, y0max + 1):
                for x0 in range(x0min, x0max + 1):
                    cand = prev_gray[y0:y0+B, x0:x0+B]
                    sad = np.abs(block.astype(np.int32) - cand.astype(np.int32)).sum()
                    if sad < best_sad:
                        best_sad = sad
                        best_dy = y0 - by
                        best_dx = x0 - bx
            mv[by // B, bx // B] = (best_dy, best_dx)
    return mv

def _warp_by_blocks(prev_bgr, mv, B=16):
    H, W, _ = prev_bgr.shape
    out = prev_bgr.copy()
    for by in range(0, H, B):
        if by + B > H: break
        for bx in range(0, W, B):
            if bx + B > W: break
            dy, dx = mv[by // B, bx // B]
            y0 = np.clip(by + dy, 0, H - B)
            x0 = np.clip(bx + dx, 0, W - B)
            out[by:by+B, bx:bx+B] = prev_bgr[y0:y0+B, x0:x0+B]
    return out

def process(input_path: str, output_path: str, alpha=0.85, block=16, radius=8,
            gop=250, codec="libx264", verbose=False):
    """
    Naive block matching (SAD). Warps previous frame blockwise to predict current.
    Chunky, classic blocky drift. Slow at high resolutions.
    """
    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open {input_path}")
    w, h, fps = _video_props(cap)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(output_path, fourcc, fps, (w, h))
    if not writer.isOpened():
        raise RuntimeError(f"Could not open writer for {output_path}")

    ok, prev_bgr = cap.read()
    if not ok:
        cap.release(); writer.release()
        raise RuntimeError("Empty input video.")
    prev_gray = cv2.cvtColor(prev_bgr, cv2.COLOR_BGR2GRAY)

    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or None
    pbar = tqdm(total=total, desc="blockmatch_basic", disable=not verbose, smoothing=0.1)

    try:
        writer.write(prev_bgr)
        if total: pbar.update(1)

        while True:
            ok, cur_bgr = cap.read()
            if not ok:
                break
            cur_gray = cv2.cvtColor(cur_bgr, cv2.COLOR_BGR2GRAY)

            mv = _block_match(prev_gray, cur_gray, B=int(block), R=int(radius))
            pred = _warp_by_blocks(prev_bgr, mv, B=int(block))

            writer.write(pred)

            prev_bgr = cur_bgr
            prev_gray = cur_gray
            pbar.update(1)
    finally:
        pbar.close()
        cap.release()
        writer.release()
