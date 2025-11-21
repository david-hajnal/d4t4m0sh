# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

d4t4m0sh is a video datamoshing tool that creates glitch art effects by manipulating video codec compression. It works by removing I-frames (keyframes) and manipulating P-frames to create abstract visual artifacts like motion trails, frozen frames, and cross-clip bleeding.

## Setup

```bash
./setup_mosh_mac.sh
source mosh-venv/bin/activate
```

This installs FFmpeg (via Homebrew), creates a Python venv, and installs dependencies: numpy, opencv-python-headless, av (PyAV), tqdm.

## Running the Tool

```bash
python main.py -f INPUT -a ALGORITHM [-o OUTPUT] [OPTIONS]
```

### Common Commands

**Basic datamosh (I-frame drop):**
```bash
python main.py -f input.mp4 -a gop_iframe_drop -o out.mp4 -v
```

**Multi-clip datamosh (most common for artistic use):**
```bash
python main.py -f video1.mp4,video2.mp4 -a bergman_style -o out.mp4 --postcut 6 -v
```

**Strongest artifacts (requires Xvid/AVI inputs):**
```bash
./convert_to_xvid.sh -i videosrc -o xvid_out
python main.py -f xvid_out/clip1.xvid.avi,xvid_out/clip2.xvid.avi -a avidemux_style -o out.avi -v
```

**Analyze GOP structure:**
```bash
python main.py -f video.mp4 -a inspect_gop -o analysis.csv -v
```

## Architecture

### Algorithm Registry Pattern

Algorithms are registered in `mosh_algorithms/__init__.py` as a dict mapping names to `process()` functions. `main.py` routes CLI calls to algorithms and uses introspection to pass only relevant parameters.

### Two Processing Approaches

**Decoded-Domain** (OpenCV/NumPy): Algorithms like `flow_leaky`, `gop_iframe_drop`, `blockmatch_basic` decode frames, manipulate pixel arrays, then re-encode.

**Packet-Domain** (FFmpeg/PyAV): Algorithms like `bergman_style`, `avidemux_style` use multi-stage FFmpeg pipelines with packet-level manipulation. Pattern:
1. Normalize clips (uniform WxH, FPS, codec)
2. Concatenate with keyframes only at clip boundaries
3. Detect and remove I-frames (packet-level or frame dropping)
4. Final encode with long GOP

All intermediate files use `tempfile.TemporaryDirectory()` for automatic cleanup.

## Key Algorithms

- **`gop_iframe_drop`**: Simple I-frame duplication (classic freeze effect)
- **`flow_leaky`**: Optical flow warping (smooth trails, no codec manipulation)
- **`bergman_style`**: Multi-clip datamosh with quality control (most common for art)
- **`avidemux_style`**: Packet-level remux without re-encoding (strongest artifacts, requires Xvid/AVI)
- **`video_to_image_mosh`/`image_to_video_mosh`**: Transition effects between video and stills
- **`ui_keyframe_editor`**: Interactive curses UI for manual I-frame selection

## Important Parameters

- `--postcut N`: Frames to drop after each removed I-frame (default 6)
- `--mosh_q N`: MPEG-4 quantizer for bergman_style (higher = blockier, default 8)
- `--alpha N`: Flow blend factor for flow_leaky (default 0.85)
- `--block N`: Block size for blockmatch_basic (default 16)

## Adding New Algorithms

1. Create `mosh_algorithms/your_algo.py` with `process(input_path, output_path, **kwargs)` function
2. Register in `mosh_algorithms/__init__.py`: `ALGORITHMS['your_algo'] = your_algo.process`
3. CLI args automatically mapped via function signature introspection

## Critical Requirements

- **avidemux_style**: All inputs must be identical Xvid/AVI format (same WxH, FPS, yuv420p). Use `convert_to_xvid.sh` to normalize first.
- **FFmpeg/ffprobe**: Must be in PATH. Multi-clip algorithms use subprocess calls.
- **blockmatch_basic**: Extremely slow (O(n²) search). Use only for small test clips.
