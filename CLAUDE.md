# CLAUDE.md

This file is a practical guide for coding agents working in this repository.

## Project Summary

d4t4m0sh is a Python + FFmpeg datamosh toolkit. It combines:
- A registry-driven CLI (`main.py`) for modular algorithms in `mosh_algorithms/`
- An interactive wizard (`wizard.py`) for guided runs
- Standalone scripts for specialized workflows (`aviglitch_mosh.py`, `mosh.py`, `mosh_h264.py`, `mosh_zoom_oneclip.py`)

Core idea: manipulate GOP/keyframe behavior or packets to create datamosh artifacts.

## Setup

Preferred on macOS:

```bash
./setup_mosh_mac.sh
source mosh-venv/bin/activate
```

Notes:
- There is no `requirements.txt` in this repo.
- The setup script installs `numpy`, `opencv-python-headless`, `av`, and `tqdm`.
- `ffmpeg` and `ffprobe` must be on `PATH`.

## Main Entry Points

- `main.py`: primary CLI dispatcher for algorithms in `mosh_algorithms/__init__.py`
- `wizard.py`: interactive flow for selecting algorithm, files, options, output, then execute
- `aviglitch_mosh.py`: direct AVI packet manipulation workflow with optional prep conversion
- `mosh.py`: packet-surgery transition between clip A and clip B
- `mosh_h264.py`: H.264 long-GOP transition workflow
- `mosh_zoom_oneclip.py`: single-clip melting zoom workflow

## Current Algorithm Registry (`main.py`)

`main.py -a` choices come from `mosh_algorithms/__init__.py`:

- `gop_iframe_drop`
- `flow_leaky`
- `blockmatch_basic`
- `inspect_gop`
- `gop_multi_drop_concat`
- `ui_keyframe_editor`
- `video_to_image_mosh`
- `image_to_video_mosh`
- `avidemux_style`
- `avidemux_style_all`
- `randomizer`
- `double_exposure`

## Current Wizard Menu (`wizard.py`)

The wizard exposes a curated set (not identical to `main.py`):

- `inspect_gop`
- `gop_multi_drop_concat`
- `video_to_image_mosh`
- `image_to_video_mosh`
- `double_exposure`
- `avidemux_style`
- `avidemux_style_all`
- `mosh_zoom_oneclip`
- `aviglitch_mosh`

## Usage Patterns

Wizard first:

```bash
python3 wizard.py
```

CLI registry path:

```bash
python3 main.py -a inspect_gop -f videosrc/clip.mp4 -o clip.gop.csv -v
python3 main.py -a gop_multi_drop_concat -f "videosrc/a.mp4,videosrc/b.mp4" -o out.mp4 -v
python3 main.py -a avidemux_style -f "xvid_out/a.xvid.avi,xvid_out/b.xvid.avi" -o out.avi -v
```

Standalone scripts:

```bash
python3 aviglitch_mosh.py --in videosrc/clip.mp4 --out clip.moshed.avi --prep --drop-start 2.0 --drop-end 4.0 -v
python3 mosh_zoom_oneclip.py --in videosrc/clip.mp4 --out clip.zoom.mp4 -v
```

## Architecture Notes

- `main.py` builds a common argument set, then filters args by each algorithm's `process(...)` signature with `inspect.signature`.
- Multi-input handling in `main.py` accepts comma-separated `-f` or interactive scan/pick from `videosrc/`.
- Most heavy video work is done through FFmpeg subprocess pipelines and PyAV packet/frame handling.
- `wizard.py` has its own metadata catalogs (`ALGORITHM_INFO`, `OPTION_INFO`) and command builders.

## Guardrails and Gotchas

- Keep docs and registries aligned when adding/removing algorithms:
  - `mosh_algorithms/__init__.py`
  - `main.py` help text and examples
  - `wizard.py` (`ALGORITHM_INFO` and option mapping)
  - `README.md` and this file
- `avidemux_style` expects pre-converted, compatible Xvid AVI inputs for strongest/cleanest packet surgery behavior.
- `aviglitch_mosh.py` requires at least one operation:
  - I-frame window (`--drop-start` + `--drop-end`), or
  - P-frame duplication (`--dup-at`)
- Large media artifacts live in the repo root; avoid accidental edits/removals unless explicitly requested.

## Minimal Validation After Changes

For Python file edits:

```bash
python3 -m py_compile main.py wizard.py aviglitch_mosh.py
```

For logic changes in one module, compile that module at minimum:

```bash
python3 -m py_compile path/to/changed_file.py
```
