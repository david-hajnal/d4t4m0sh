import av
from tqdm import tqdm

def process(input_path: str, output_path: str, alpha=0.85, block=16, radius=8,
            gop=250, codec="libx264", verbose=False):
    """
    Decode with PyAV. When encountering a keyframe in the source, *duplicate the previous*
    frame instead of using the keyframe. Re-encode with long GOP and no B-frames.

    Tips:
      - For stronger moshing, pre-transcode sources to minimize I-frames & disable B-frames.
      - This re-encodes; it does not perform packet-level remux surgery.
    """
    in_container = av.open(input_path)
    v_in = next(s for s in in_container.streams if s.type == "video")

    # FPS / timebase
    rate = v_in.average_rate or v_in.base_rate or 30

    out_container = av.open(output_path, mode="w")
    v_out = out_container.add_stream(codec, rate=rate)
    v_out.width = v_in.codec_context.width
    v_out.height = v_in.codec_context.height
    v_out.pix_fmt = "yuv420p"
    try:
        v_out.codec_context.gop_size = int(gop)
    except Exception:
        pass
    try:
        v_out.codec_context.max_b_frames = 0
    except Exception:
        pass

    last_frame = None
    total = getattr(v_in, "frames", 0) or None
    pbar = tqdm(total=total, desc="gop_iframe_drop", disable=not verbose)

    try:
        for frame in in_container.decode(v_in):
            if frame.key_frame and last_frame is not None:
                out_frame = last_frame
            else:
                out_frame = frame
                last_frame = frame

            if out_frame.format.name != "yuv420p":
                out_frame = out_frame.reformat(format="yuv420p")

            for packet in v_out.encode(out_frame):
                out_container.mux(packet)
            pbar.update(1)
    finally:
        pbar.close()
        for packet in v_out.encode(None):
            out_container.mux(packet)
        out_container.close()
        in_container.close()
