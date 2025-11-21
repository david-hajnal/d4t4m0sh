# mosh_algorithms/randomizer.py
import os
import json
import subprocess
import tempfile
import random

def _run(cmd, verbose=False):
    """Execute ffmpeg command with proper logging."""
    loglevel = "info" if verbose else "error"
    if cmd and cmd[0] == "ffmpeg" and "-loglevel" not in cmd:
        cmd = cmd[:1] + ["-hide_banner", "-loglevel", loglevel] + cmd[1:]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg exit {p.returncode}\nCMD: {' '.join(cmd)}\n{p.stderr}")
    if verbose and p.stderr:
        print(p.stderr)
    return p

def _ffprobe(path):
    """Get video metadata: width, height, fps, duration."""
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,avg_frame_rate:format=duration",
        "-of", "json", path
    ]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}:\n{p.stderr}")
    data = json.loads(p.stdout)
    s = data["streams"][0]
    w, h = int(s["width"]), int(s["height"])
    fr = s.get("avg_frame_rate", "0/0")
    try:
        n, d = fr.split("/")
        fps = float(n) / float(d) if d != "0" else 30.0
    except Exception:
        fps = 30.0

    # Get duration from format
    duration = float(data.get("format", {}).get("duration", 0))

    return w, h, fps, duration

def _safe_fps_str(fps):
    """Convert fps to ffmpeg-friendly string."""
    if abs(fps - 23.976) < 0.05: return "24000/1001"
    if abs(fps - 29.97)  < 0.05: return "30000/1001"
    if abs(fps - 59.94)  < 0.1:  return "60000/1001"
    return str(max(1, int(round(fps))))

def process(input_path: str, output_path: str, chunk_length: float = 2.0,
            codec: str = "libx264", verbose: bool = False, **kwargs):
    """
    Randomizer algorithm:
      1) Reads input video and determines total duration
      2) Splits video into chunks of specified length (in seconds)
      3) Randomly reorders the chunks
      4) Concatenates them back together

    Args:
        input_path: Path to input video file
        output_path: Path to output video file
        chunk_length: Duration of each chunk in seconds (default: 2.0)
        codec: Video codec to use (default: libx264)
        verbose: Enable verbose logging
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    # Get video metadata
    w, h, fps, duration = _ffprobe(input_path)
    w = (w // 2) * 2  # Ensure even dimensions
    h = (h // 2) * 2
    fps_str = _safe_fps_str(fps)

    if duration <= 0:
        raise ValueError(f"Invalid video duration: {duration}")

    if chunk_length <= 0:
        raise ValueError(f"Chunk length must be positive, got: {chunk_length}")

    # Calculate number of chunks
    num_chunks = int(duration / chunk_length)
    if num_chunks < 2:
        raise ValueError(f"Video too short ({duration}s) for chunk length {chunk_length}s. Need at least 2 chunks.")

    if verbose:
        print(f"[INFO] Video duration: {duration:.2f}s")
        print(f"[INFO] Chunk length: {chunk_length}s")
        print(f"[INFO] Number of chunks: {num_chunks}")

    with tempfile.TemporaryDirectory(prefix="randomizer_") as tmp:
        # Step 1: Split video into chunks
        chunk_files = []
        if verbose:
            print(f"[INFO] Splitting video into {num_chunks} chunks...")

        for i in range(num_chunks):
            start_time = i * chunk_length
            chunk_path = os.path.join(tmp, f"chunk_{i:04d}.mp4")

            _run([
                "ffmpeg", "-y", "-i", input_path,
                "-ss", str(start_time),
                "-t", str(chunk_length),
                "-c:v", codec,
                "-c:a", "aac" if output_path.lower().endswith((".mp4", ".mov", ".m4v")) else "copy",
                "-pix_fmt", "yuv420p",
                "-r", fps_str,
                chunk_path
            ], verbose=verbose)

            chunk_files.append(chunk_path)

        # Step 2: Randomize the order
        random_order = list(range(num_chunks))
        random.shuffle(random_order)

        if verbose:
            print(f"[INFO] Random order: {random_order}")

        # Step 3: Create concat file
        concat_file = os.path.join(tmp, "concat_list.txt")
        with open(concat_file, "w") as f:
            for idx in random_order:
                # Use relative path for concat demuxer
                f.write(f"file 'chunk_{idx:04d}.mp4'\n")

        if verbose:
            print(f"[INFO] Concatenating chunks in random order...")

        # Step 4: Concatenate chunks
        _run([
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_file,
            "-c", "copy",
            output_path
        ], verbose=verbose)

    if verbose:
        print(f"[OK] Randomized video written to {output_path}")