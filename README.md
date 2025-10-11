# d4t4m0sh

Tiny toolkit of Python + FFmpeg “datamosh” algorithms. It supports both classic AVI/Xvid packet surgery (Avidemux-style, no re-encode) and FFmpeg-only approaches, plus a terminal UI to interactively remove keyframes.

* Inspired by classic Avidemux 2.5.x keyframe-deletion workflows and community tutorials.

## Features
- Drop I-frames and manipulate GOP structure
- Convert videos to Xvid and other formats
- Concatenate and process multiple video files
- Frame-by-frame video inspection and editing
- Batch processing scripts for automation
- Python modules for custom video mosh algorithms

## Directory Structure
- `main.py` — Main entry point for running scripts
- `mosh_algorithms/` — Python modules implementing various video mosh and manipulation algorithms
- `videosrc/` — Source video files for processing
- `xvid_out/` — Output directory for processed videos
- Shell scripts (`*.sh`) — Utilities for conversion, frame dropping, and setup

## Requirements
- Python 3.12+
- [PyAV](https://github.com/PyAV-Org/PyAV)
- [OpenCV](https://opencv.org/)
- [NumPy](https://numpy.org/)
- [tqdm](https://tqdm.github.io/)
- Xvid codec (for certain scripts)

You can use the provided `mosh-venv/` virtual environment or set up your own.

## Setup
1. Clone the repository:
   ```sh
   git clone https://github.com/david-hajnal/d4t4m0sh.git
   cd d4t4m0sh
   ```
2. (Optional) Activate the provided virtual environment:
   ```sh
    python -m venv mosh-venv
    source mosh-venv/bin/activate
    pip install -r requirements.txt
    ```

## Quick Start (Interactive Wizard)

**New users: try the wizard first!** It guides you through algorithm selection and options with detailed explanations.

```bash
python wizard.py
```

The wizard will:
- Show all algorithms grouped by category with descriptions
- Explain what each algorithm does and when to use it
- Guide you through file selection
- Configure options with helpful tooltips
- Show command summary before execution

# Features

- Multiple algorithms (pick via -a <name>):

    - inspect_gop – print/save per-frame types (I/P/B) to check your GOPs.

    - gop_iframe_drop – remove I-frames from a single clip (FFmpeg re-encode).

    - gop_multi_drop_concat – normalize → concat → force boundary I → drop I (+ --postcut) → encode.

    - ui_keyframe_editor – curses TUI to toggle/delete I-frames, jump between keyframes, preview frames, set postcut, then encode.

    - video_to_image_mosh – smear video → still image (builds a motion clip from a still).

    - image_to_video_mosh – smear still image → video.

    - avidemux_style – no re-encode, packet surgery on pre-Xvid AVIs (video-only output).

    - avidemux_style_all – one-shot: convert → concat(copy) → packet surgery → deliver (AVI or MP4; supports audio import).

- macOS-friendly: works with Homebrew FFmpeg; prefers software x264 for MP4 delivery.

# Usage

## Option 1: Interactive Wizard (Recommended for beginners)

```bash
python wizard.py
```

## Option 2: Command Line

```bash
python main.py -a <algorithm> [-f "file1,file2,..."] -o out.ext [options]
```

* -a algorithm name (see Features)

* -f comma-separated inputs; if omitted, you'll select from ./videosrc

* -o output path (.avi recommended for strongest artifacts)

* -v verbose FFmpeg logs

## Datamosh knobs

* --gop N – keyframe interval (longer = fewer I’s). For MPEG-4 ASP, cap ≈ 600.

* --postcut N – drop N frames/packets after each removed I (bigger = stronger smear).

* --postcut-rand A:B – randomize postcut per boundary (A..B).

* --drop_mode [all_after_first|boundaries_only] – packet surgery strategy.

* --mosh_q Q – MPEG-4 quantizer (higher = blockier = more smear). Try 8–12.

* --hold_sec S – add a “smear hold” (frame clone) to the end of each clip before joins.

* --codec libx264|h264_videotoolbox|… – final encoder when output is MP4 (prefer libx264).

* --image PATH, --img_dur SEC, --kb rotate|zoom_in – for the image↔video presets.

* --audio_from PATH – add audio (for avidemux_style_all when delivering MP4).

## Avidemux

Strongest, old-school mosh

```bash
# 1) Convert sources to Xvid/AVI (even WxH, CFR, no B-frames, long GOP)
./convert_to_xvid.sh -i videosrc -o xvid_out -q 10 -g 600 -r 30 -a none

# 2) Packet-surgery (no re-encode), video-only AVI
python main.py -a avidemux_style \
  -f "xvid_out/a.xvid.avi,xvid_out/b.xvid.avi" \
  --postcut 10 --postcut-rand 6:12 --drop_mode boundaries_only \
  -o out.avi -v
  
```

All-in-one (convert ➜ concat ➜ surgery ➜ deliver)

```bash
# Strong mosh with smear holds and random postcut; deliver AVI
python main.py -a avidemux_style_all -v \
  --mosh_q 12 --gop 600 --hold_sec 0.8 \
  --postcut-rand 8:14 --drop_mode all_after_first \
  -o out.avi

# Deliver MP4 with audio from the first original clip
python main.py -a avidemux_style_all -v \
  --mosh_q 12 --hold_sec 1.0 --postcut 12 \
  --audio_from videosrc/first_source.mp4 \
  -o out.mp4
```

If a tool complains about AVI index, you can rebuild

```bash
ffmpeg -y -fflags +genpts -i out.avi -c copy out_fixed.avi
```

## License
MIT License
