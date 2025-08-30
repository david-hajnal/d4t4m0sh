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

def process(input_path: str, output_path: str, alpha=0.85, block=16, radius=8,
            gop=250, codec="libx264", verbose=False):
    """
    Dense optical flow (Farnebäck). Warp an accumulator by *backward* flow (cur->prev)
    and blend with the current frame → smeary moshing without codec surgery.
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
    acc = prev_bgr.astype(np.float32)

    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or None
    pbar = tqdm(total=total, desc="flow_leaky", disable=not verbose, smoothing=0.1)

    try:
        while True:
            ok, cur_bgr = cap.read()
            if not ok:
                break

            cur_gray = cv2.cvtColor(cur_bgr, cv2.COLOR_BGR2GRAY)

            # Backward flow: from current to previous
            flow = cv2.calcOpticalFlowFarneback(
                cur_gray, prev_gray, None,
                pyr_scale=0.5, levels=3, winsize=15,
                iterations=3, poly_n=5, poly_sigma=1.2, flags=0
            )

            H, W = cur_gray.shape
            grid_x, grid_y = np.meshgrid(np.arange(W, dtype=np.float32),
                                         np.arange(H, dtype=np.float32))
            map_x = grid_x + flow[..., 0]
            map_y = grid_y + flow[..., 1]

            warped_prev_acc = cv2.remap(
                acc, map_x, map_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
            )

            acc = (alpha * warped_prev_acc + (1.0 - alpha) * cur_bgr).astype(np.float32)
            out = np.clip(acc, 0, 255).astype(np.uint8)
            writer.write(out)

            prev_gray = cur_gray
            pbar.update(1)
    finally:
        pbar.close()
        cap.release()
        writer.release()
