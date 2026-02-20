import os
import subprocess
from typing import Dict


PRESETS: Dict[str, Dict[str, float]] = {
    "urban_grit": {
        "contrast": 1.35,
        "brightness": -0.03,
        "saturation": 0.85,
        "gamma": 0.92,
        "rs": -0.02,
        "gs": 0.04,
        "bs": 0.10,
        "rm": 0.05,
        "gm": -0.01,
        "bm": -0.04,
        "rh": 0.08,
        "gh": 0.02,
        "bh": -0.06,
        "unsharp": 0.80,
        "grain": 14.0,
        "vignette": 1.0,
        "ghost": 0.0,
        "blur": 0.0,
    },
    "dirty_glass": {
        "contrast": 1.10,
        "brightness": 0.02,
        "saturation": 0.75,
        "gamma": 0.95,
        "rs": -0.01,
        "gs": 0.06,
        "bs": 0.08,
        "rm": 0.00,
        "gm": 0.03,
        "bm": 0.02,
        "rh": 0.05,
        "gh": 0.02,
        "bh": -0.03,
        "unsharp": 0.25,
        "grain": 20.0,
        "vignette": 0.6,
        "ghost": 0.15,
        "blur": 0.3,
    },
    "faded_teal_amber": {
        "contrast": 1.08,
        "brightness": 0.04,
        "saturation": 0.70,
        "gamma": 1.05,
        "rs": -0.03,
        "gs": 0.02,
        "bs": 0.12,
        "rm": -0.01,
        "gm": 0.02,
        "bm": 0.06,
        "rh": 0.10,
        "gh": 0.04,
        "bh": -0.08,
        "unsharp": 0.20,
        "grain": 10.0,
        "vignette": 0.35,
        "ghost": 0.05,
        "blur": 0.0,
    },
    "hard_shadow_split": {
        "contrast": 1.50,
        "brightness": -0.08,
        "saturation": 0.90,
        "gamma": 0.88,
        "rs": -0.06,
        "gs": 0.02,
        "bs": 0.10,
        "rm": 0.03,
        "gm": -0.01,
        "bm": 0.03,
        "rh": 0.14,
        "gh": 0.02,
        "bh": -0.10,
        "unsharp": 1.20,
        "grain": 12.0,
        "vignette": 0.9,
        "ghost": 0.0,
        "blur": 0.0,
    },
}


def _run(cmd, verbose=False):
    if cmd and cmd[0] == "ffmpeg" and "-loglevel" not in cmd:
        loglevel = "info" if verbose else "error"
        cmd = cmd[:1] + ["-hide_banner", "-loglevel", loglevel] + cmd[1:]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg exit {p.returncode}\nCMD: {' '.join(cmd)}\n{p.stderr}")
    if verbose and p.stderr:
        print(p.stderr)
    return p


def _clamp(value, low, high):
    return max(low, min(high, value))


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _scale_from_neutral(base, neutral, strength):
    return neutral + (base - neutral) * strength


def _fmt(value):
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _build_filter_chain(preset_name, fx_strength, grain, vignette, ghost):
    p = PRESETS[preset_name]
    s = _clamp(float(fx_strength), 0.0, 2.0)

    contrast = _scale_from_neutral(p["contrast"], 1.0, s)
    brightness = _scale_from_neutral(p["brightness"], 0.0, s)
    saturation = _scale_from_neutral(p["saturation"], 1.0, s)
    gamma = _scale_from_neutral(p["gamma"], 1.0, s)

    cb_keys = ("rs", "gs", "bs", "rm", "gm", "bm", "rh", "gh", "bh")
    cb_vals = {k: _scale_from_neutral(p[k], 0.0, s) for k in cb_keys}

    unsharp = _clamp(p["unsharp"] * s, 0.0, 3.0)
    blur = _clamp(p["blur"] * s, 0.0, 3.0)

    if grain is None or float(grain) < 0:
        grain_amt = int(round(_clamp(p["grain"] * s, 0.0, 60.0)))
    else:
        grain_amt = int(round(_clamp(float(grain), 0.0, 60.0)))

    if vignette is None or float(vignette) < 0:
        vignette_amt = _clamp(p["vignette"] * s, 0.0, 2.0)
    else:
        vignette_amt = _clamp(float(vignette), 0.0, 2.0)

    if ghost is None or float(ghost) < 0:
        ghost_amt = _clamp(p["ghost"] * s, 0.0, 1.0)
    else:
        ghost_amt = _clamp(float(ghost), 0.0, 1.0)

    filters = [
        (
            "eq="
            f"contrast={_fmt(contrast)}:"
            f"brightness={_fmt(brightness)}:"
            f"saturation={_fmt(saturation)}:"
            f"gamma={_fmt(gamma)}"
        ),
        "colorbalance=" + ":".join(f"{k}={_fmt(v)}" for k, v in cb_vals.items()),
    ]

    if blur > 0.01:
        filters.append(f"gblur=sigma={_fmt(blur)}")
    if unsharp > 0.01:
        filters.append(f"unsharp=5:5:{_fmt(unsharp)}:3:3:0")
    if grain_amt > 0:
        filters.append(f"noise=alls={grain_amt}:allf=t+u")
    if vignette_amt > 0.01:
        angle = _fmt(_clamp(3.6 - (vignette_amt * 1.4), 1.6, 3.6))
        filters.append(f"vignette=angle=PI/{angle}")
    if ghost_amt > 0.01:
        filters.append(f"tblend=all_mode=average:all_opacity={_fmt(_clamp(ghost_amt, 0.0, 0.95))}")

    filters.append("format=yuv420p")
    return ",".join(filters)


def process(
    input_path: str,
    output_path: str,
    alpha=0.85,
    block=16,
    radius=8,
    gop=250,
    codec="libx264",
    verbose=False,
    image=None,
    img_dur=None,
    kb_mode=None,
    postcut=6,
    color_preset="urban_grit",
    fx_strength=1.0,
    grain=-1,
    vignette=-1.0,
    ghost=-1.0,
    keep_audio=True,
):
    """
    Apply preset-based cinematic color effects using FFmpeg filters.
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")
    if "," in input_path:
        raise ValueError("color_fx_ffmpeg accepts exactly one input file")
    if color_preset not in PRESETS:
        available = ", ".join(sorted(PRESETS.keys()))
        raise ValueError(f"Unknown color_preset '{color_preset}'. Available: {available}")
    if str(codec).lower() == "copy":
        raise ValueError("codec='copy' is not valid when using video filters")

    gop = int(gop)
    if gop < 1:
        raise ValueError("gop must be >= 1")

    keep_audio = _as_bool(keep_audio)
    vf = _build_filter_chain(color_preset, fx_strength, grain, vignette, ghost)

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-vf",
        vf,
        "-map",
        "0:v:0",
        "-c:v",
        str(codec),
        "-g",
        str(gop),
        "-pix_fmt",
        "yuv420p",
    ]

    if keep_audio:
        cmd.extend(["-map", "0:a?"])
        ext = os.path.splitext(output_path)[1].lower()
        if ext in {".mp4", ".mov", ".m4v"}:
            cmd.extend(["-c:a", "aac", "-b:a", "192k"])
        else:
            cmd.extend(["-c:a", "copy"])
    else:
        cmd.append("-an")

    cmd.append(output_path)
    _run(cmd, verbose=verbose)

