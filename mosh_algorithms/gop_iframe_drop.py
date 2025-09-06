import av
import cv2
import numpy as np
from tqdm import tqdm

def process(input_path: str, output_path: str, alpha=0.85, block=16, radius=8,
            gop=250, codec="libx264", verbose=False):
    """
    'GOP-ish' datamosh:
      - Decode with PyAV to detect keyframes.
      - When an I-frame is encountered, write the *previous* frame instead (duplicate).
      - Encode using OpenCV (mp4v) to avoid PyAV's MP4 writer issues on macOS.

    Notes:
      - This produces the classic 'freeze-on-I' visual effect.
      - For true packet-level GOP surgery (smearing P-frames across a held reference),
        we'd need container/packet editing; this decoded-domain trick is stable and fast.
    """
    # --- open input with PyAV
    with av.open(input_path) as in_container:
        v_in = next(s for s in in_container.streams if s.type == "video")
        rate = v_in.average_rate or v_in.base_rate
        fps = float(rate) if rate else 30.0
        w = v_in.codec_context.width
        h = v_in.codec_context.height

        # --- open output with OpenCV
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(output_path, fourcc, fps, (w, h))
        if not writer.isOpened():
            raise RuntimeError(f"Could not open writer for {output_path}")

        last_bgr = None
        total = getattr(v_in, "frames", 0) or None
        pbar = tqdm(total=total, desc="gop_iframe_drop", disable=not verbose)

        try:
            for frame in in_container.decode(v_in):
                # current frame as BGR
                cur_bgr = frame.to_ndarray(format="bgr24")

                if frame.key_frame and last_bgr is not None:
                    # on I-frame: duplicate previous frame instead of showing the I-frame
                    writer.write(last_bgr)
                else:
                    writer.write(cur_bgr)
                    last_bgr = cur_bgr

                pbar.update(1)
        finally:
            pbar.close()
            writer.release()
