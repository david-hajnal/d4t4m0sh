#!/usr/bin/env python3
"""
Interactive wizard for d4t4m0sh
Guides users through algorithm selection and configuration with detailed explanations.
"""
import os
import sys
import subprocess
from typing import List, Dict, Any, Optional

VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".gif")

# Algorithm metadata with detailed descriptions
ALGORITHM_INFO = {
    "inspect_gop": {
        "name": "GOP Inspector",
        "category": "analysis",
        "desc": "Analyze video GOP (Group of Pictures) structure. Shows frame types (I/P/B) to understand keyframe distribution.",
        "use_case": "Use this first to see what you're working with - how many I-frames, GOP length, etc.",
        "inputs": "single",
        "outputs": ".csv",
        "options": []
    },
    "gop_iframe_drop": {
        "name": "Simple I-Frame Drop",
        "category": "basic",
        "desc": "Removes I-frames (keyframes) from a single video. Re-encodes with FFmpeg.",
        "use_case": "Quick datamosh effect on one clip. Good for testing, but Avidemux methods are stronger.",
        "inputs": "single",
        "outputs": ".mp4/.avi",
        "options": ["gop", "codec", "verbose"]
    },
    "gop_multi_drop_concat": {
        "name": "Multi-Clip Concat & Drop",
        "category": "basic",
        "desc": "Concatenates multiple clips, forces keyframes at boundaries, then drops them. Supports smear holds and random postcut.",
        "use_case": "Blend multiple clips together with motion smearing. Good for music videos.",
        "inputs": "multiple",
        "outputs": ".mp4/.avi",
        "options": ["gop", "codec", "postcut", "postcut_rand", "hold_sec", "verbose"]
    },
    "ui_keyframe_editor": {
        "name": "Interactive Keyframe Editor",
        "category": "advanced",
        "desc": "Curses-based TUI for frame-by-frame control. Toggle I-frames, jump between keyframes, preview frames, adjust postcut.",
        "use_case": "Surgical precision - manually choose which keyframes to remove for artistic control.",
        "inputs": "single",
        "outputs": ".mp4/.avi",
        "options": ["gop", "codec", "verbose"]
    },
    "video_to_image_mosh": {
        "name": "Video → Image Mosh",
        "category": "creative",
        "desc": "Smears video motion INTO a still image. Creates trippy effect where the image seems to move.",
        "use_case": "Turn a portrait or artwork into an animated, glitchy piece with motion from your video.",
        "inputs": "single",
        "outputs": ".mp4/.avi",
        "options": ["image", "img_dur", "kb_mode", "gop", "codec", "verbose"]
    },
    "image_to_video_mosh": {
        "name": "Image → Video Mosh",
        "category": "creative",
        "desc": "Creates motion from a still image, then smears it into video. Image appears to flow/melt.",
        "use_case": "Animate a static image with artificial motion (rotation, zoom), then datamosh it.",
        "inputs": "single",
        "outputs": ".mp4/.avi",
        "options": ["image", "img_dur", "kb_mode", "gop", "codec", "verbose"]
    },
    "avidemux_style": {
        "name": "Avidemux-Style Surgery (manual prep)",
        "category": "avidemux",
        "desc": "Pure packet surgery, NO re-encode. Works on pre-converted Xvid AVI files. Strongest artifacts.",
        "use_case": "Old-school, maximum glitch. You must first convert clips to Xvid with convert_to_xvid.sh.",
        "inputs": "multiple",
        "outputs": ".avi (video only)",
        "options": ["postcut", "postcut_rand", "drop_mode", "verbose"]
    },
    "avidemux_style_all": {
        "name": "Avidemux-Style All-In-One",
        "category": "avidemux",
        "desc": "One-shot: convert → concat → packet surgery → deliver. Can output AVI or MP4 with audio.",
        "use_case": "Easiest way to get strong Avidemux-style mosh. Handles everything automatically.",
        "inputs": "multiple",
        "outputs": ".avi or .mp4",
        "options": ["mosh_q", "gop", "hold_sec", "postcut", "postcut_rand", "drop_mode", "codec", "audio_from", "verbose"]
    },
    "mosh": {
        "name": "P-Cascade Bloom Transition (Packet Surgery)",
        "category": "transitions",
        "desc": "EXTREME 'P-cascade bloom' datamosh transition using packet surgery. Creates dramatic melting effect where clip B begins.",
        "use_case": "Maximum artifact strength for music video transitions - pure packet manipulation for extreme smear effects.",
        "inputs": "two",
        "outputs": ".avi and .mp4",
        "options": ["fps", "width", "mosh_q", "gop_len", "no_iframe_window", "postcut", "repeat_boost", "repeat_times"]
    },
    "mosh_zoom_oneclip": {
        "name": "Melting Zoom (Single Clip)",
        "category": "creative",
        "desc": "Artificial zoom + datamosh on a single clip. Freezes a frame, generates zoom ramp, forces P-cascade for 'melting zoom' effect.",
        "use_case": "Create trippy zoom effects that melt/smear. Great for beat drops, time stretching, or psychedelic visuals.",
        "inputs": "single",
        "outputs": ".mp4",
        "options": ["fps", "width", "zoom_q", "zoom_t", "zoom_dur", "zoom_tail", "zoom_direction", "deliver_crf"]
    },
    "mosh_h264": {
        "name": "H.264 Long-GOP Transition (Modern)",
        "category": "transitions",
        "desc": "Modern H.264 datamosh with IDR strip and SEI removal. Better compatibility than Xvid with extreme artifacts.",
        "use_case": "Best of both worlds: extreme glitch effects with modern H.264 compression and universal playback.",
        "inputs": "two",
        "outputs": ".mp4",
        "options": ["fps", "width", "h264_crf", "gop_len", "no_iframe_window", "postcut", "repeat_boost", "repeat_times"]
    },
    "ts_mosh": {
        "name": "MPEG-TS Packet-Loss Mosh",
        "category": "advanced",
        "desc": "Creates datamosh by deleting Transport Stream packets in a time window. Pure packet corruption without re-encoding.",
        "use_case": "Simulate network packet loss for realistic video corruption. Works on single clips with precise time control.",
        "inputs": "single",
        "outputs": ".mp4 or .avi",
        "options": ["ts_format", "vbitrate", "keyint", "start_ms", "duration_ms", "ts_pps", "ts_pid", "xvid_q", "fps", "verbose"]
    },
    "aviglitch_mosh": {
        "name": "AviGlitch-Style Packet Mosh",
        "category": "advanced",
        "desc": "Direct AVI packet manipulation using PyAV. I-frame removal (smear) + P-frame duplication (bloom). No re-encoding.",
        "use_case": "Classic AviGlitch workflow without Ruby. Best with MPEG-4 ASP AVI. Use --prep to auto-convert any format.",
        "inputs": "single",
        "outputs": ".avi",
        "options": ["aviglitch_prep", "prep_q", "prep_gop_ag", "prep_fps", "drop_start", "drop_end", "dup_at", "dup_count", "verbose"]
    }
}

# Option metadata with detailed help
OPTION_INFO = {
    "gop": {
        "type": "int",
        "default": 250,
        "prompt": "GOP size (keyframe interval)",
        "help": "Larger = fewer I-frames = stronger mosh. For MPEG-4, cap around 600. Try 300-600 for strong effects."
    },
    "codec": {
        "type": "choice",
        "default": "libx264",
        "choices": ["libx264", "h264_videotoolbox", "mpeg4"],
        "prompt": "Video codec for final encode",
        "help": "libx264 (software, best quality), h264_videotoolbox (macOS hardware), mpeg4 (MPEG-4 ASP)"
    },
    "postcut": {
        "type": "int",
        "default": 8,
        "prompt": "Postcut (frames to drop after each removed I-frame)",
        "help": "How many frames/packets to drop after removing a keyframe. Higher = stronger smear (try 8-14)."
    },
    "postcut_rand": {
        "type": "range",
        "default": None,
        "prompt": "Random postcut range (e.g. 6:12)",
        "help": "Randomize postcut per boundary. Format: MIN:MAX. Overrides --postcut. Adds unpredictability."
    },
    "drop_mode": {
        "type": "choice",
        "default": "all_after_first",
        "choices": ["all_after_first", "boundaries_only"],
        "prompt": "Drop mode strategy",
        "help": "all_after_first: remove ALL I-frames after first (max smear). boundaries_only: only at clip joins."
    },
    "mosh_q": {
        "type": "int",
        "default": 10,
        "prompt": "Mosh quality (Xvid/MPEG-4 quantizer)",
        "help": "1-31. Higher = blockier/grainier = more datamosh artifact. Try 10-14 for strong effects."
    },
    "postcut": {
        "type": "int",
        "default": 12,
        "prompt": "Postcut (packets to drop after I-frame removal)",
        "help": "How many packets to drop after removing each I-frame. Higher = stronger smear (try 10-20 for extreme)."
    },
    "hold_sec": {
        "type": "float",
        "default": 0.0,
        "prompt": "Smear hold duration (seconds)",
        "help": "Duplicate last frame of each clip (except final). Creates 'freeze smear' at joins. Try 0.5-1.5."
    },
    "audio_from": {
        "type": "file",
        "default": None,
        "prompt": "Audio source file (optional)",
        "help": "Path to file to extract audio from. Only for avidemux_style_all when outputting MP4."
    },
    "image": {
        "type": "file",
        "default": None,
        "prompt": "Still image path",
        "help": "Path to image file (jpg, png) for video↔image mosh algorithms."
    },
    "img_dur": {
        "type": "float",
        "default": 3.0,
        "prompt": "Image motion clip duration (seconds)",
        "help": "How long the generated image motion clip should be. Try 3-10 seconds."
    },
    "kb_mode": {
        "type": "choice",
        "default": "rotate",
        "choices": ["rotate", "zoom_in"],
        "prompt": "Image motion style",
        "help": "rotate: gentle rotation, zoom_in: slow zoom effect. Affects how the image 'moves'."
    },
    "verbose": {
        "type": "bool",
        "default": False,
        "prompt": "Verbose output",
        "help": "Show detailed FFmpeg logs during processing."
    },
    "fps": {
        "type": "int",
        "default": 30,
        "prompt": "Target framerate",
        "help": "Normalize both clips to this FPS. Standard rates: 24, 30, 60."
    },
    "width": {
        "type": "int",
        "default": 1280,
        "prompt": "Target width (height auto-scaled)",
        "help": "Width in pixels. Height maintains aspect ratio. Common: 1280, 1920."
    },
    "gop_len": {
        "type": "int",
        "default": 9999,
        "prompt": "GOP length (max keyframe distance)",
        "help": "Very high value = single long GOP = maximum smear. Use 9999 for extreme effects."
    },
    "no_iframe_window": {
        "type": "float",
        "default": 2.0,
        "prompt": "I-frame strip window duration (seconds)",
        "help": "How long after join to strip I-frames. Longer = longer melting effect. Try 2.0-4.0 for extreme."
    },
    "repeat_boost": {
        "type": "float",
        "default": 0.5,
        "prompt": "Repeat segment duration (seconds)",
        "help": "Duration after join to repeat for smear boost. Amplifies the transition. Try 0.3-1.0."
    },
    "repeat_times": {
        "type": "int",
        "default": 5,
        "prompt": "Number of repeat cycles",
        "help": "How many times to repeat the boost segment. More = heavier smear. Try 5-10 for extreme."
    },
    "zoom_q": {
        "type": "int",
        "default": 3,
        "prompt": "MPEG-4 quantizer for intermediates",
        "help": "1-31. Lower = higher quality for zoom concat. Use 3-5 for clean zoom, 8-12 for grainier effect."
    },
    "zoom_t": {
        "type": "str",
        "default": "00:00:05.000",
        "prompt": "Mosh start timestamp (HH:MM:SS.mmm or seconds)",
        "help": "When to start the zoom effect. Format: HH:MM:SS.mmm or just seconds (e.g. '5' or '5.5')."
    },
    "zoom_dur": {
        "type": "float",
        "default": 1.0,
        "prompt": "Zoom duration (seconds)",
        "help": "How long the zoom ramp lasts. Try 0.5-2.0 seconds."
    },
    "zoom_tail": {
        "type": "float",
        "default": 1.0,
        "prompt": "P-only tail duration (seconds)",
        "help": "How long the P-cascade continues after zoom. Longer = more smear. Try 1.0-3.0."
    },
    "zoom_direction": {
        "type": "choice",
        "default": "out",
        "choices": ["out", "in"],
        "prompt": "Zoom direction",
        "help": "out: push in (image gets bigger), in: pull out (image gets smaller)"
    },
    "deliver_crf": {
        "type": "int",
        "default": 18,
        "prompt": "Final H.264 CRF (quality)",
        "help": "Lower = higher quality for final MP4. 18 (high), 23 (balanced), 28 (smaller file)."
    },
    "h264_crf": {
        "type": "int",
        "default": 23,
        "prompt": "H.264 CRF (quality)",
        "help": "Lower = higher quality. 18 (visually lossless), 23 (balanced), 28 (smaller file). Try 20-25."
    },
    "vbitrate": {
        "type": "str",
        "default": "3M",
        "prompt": "Video bitrate (e.g. 3M, 5M)",
        "help": "Bitrate for MPEG-TS encoding. Higher = better quality, larger file. Use 3M-8M."
    },
    "keyint": {
        "type": "int",
        "default": 240,
        "prompt": "Keyframe interval (GOP length)",
        "help": "Frames between keyframes. Higher = longer GOP = stronger mosh potential. Try 120-500."
    },
    "start_ms": {
        "type": "int",
        "default": 5000,
        "prompt": "Corruption start time (milliseconds)",
        "help": "When to start dropping TS packets. E.g. 5000 = 5 seconds into video."
    },
    "duration_ms": {
        "type": "int",
        "default": 1200,
        "prompt": "Corruption duration (milliseconds)",
        "help": "How long to drop packets. Longer = more extreme corruption. Try 800-2000ms."
    },
    "ts_pps": {
        "type": "str",
        "default": "auto",
        "prompt": "TS packets per second (auto or number)",
        "help": "'auto' derives from bitrate. Or specify numeric value for manual control."
    },
    "ts_pid": {
        "type": "str",
        "default": None,
        "prompt": "Target PID (hex like 0x100, or leave empty)",
        "help": "Optional: drop only this video PID to preserve audio. Leave empty to drop all packets."
    },
    "ts_format": {
        "type": "choice",
        "default": "mp4",
        "choices": ["mp4", "avi"],
        "prompt": "Output format",
        "help": "mp4: codec-copy remux (fast, preserves corruption). avi: Xvid re-encode (mosh-friendly, blockier)."
    },
    "xvid_q": {
        "type": "int",
        "default": 3,
        "prompt": "Xvid quality (for AVI output)",
        "help": "1-31, lower=better. Use 3-5 for clean, 8-12 for grainier effect. Only applies to AVI output."
    },
    "aviglitch_prep": {
        "type": "bool",
        "default": False,
        "prompt": "Auto-convert to MPEG-4 ASP AVI first?",
        "help": "Convert input to glitch-friendly format (MPEG-4 ASP, long GOP, no B-frames) before moshing."
    },
    "prep_q": {
        "type": "int",
        "default": 3,
        "prompt": "Prep quality (1-31, lower=better)",
        "help": "Quality for prep conversion. 3 (high quality), 5 (balanced), 8-12 (grainier/blockier)."
    },
    "prep_gop_ag": {
        "type": "int",
        "default": 300,
        "prompt": "Prep GOP length",
        "help": "Keyframe interval for prep. Higher = longer GOP = stronger mosh potential. Try 300-600."
    },
    "prep_fps": {
        "type": "int",
        "default": 24,
        "prompt": "Prep FPS",
        "help": "Frame rate for prep conversion. Common: 24, 30, 60."
    },
    "drop_start": {
        "type": "float",
        "default": None,
        "prompt": "I-frame drop window start (seconds)",
        "help": "Start time to remove keyframes (creates smear). Leave empty to skip I-frame removal."
    },
    "drop_end": {
        "type": "float",
        "default": None,
        "prompt": "I-frame drop window end (seconds)",
        "help": "End time for keyframe removal window. Must be > drop_start."
    },
    "dup_at": {
        "type": "float",
        "default": None,
        "prompt": "P-frame duplication start time (seconds)",
        "help": "When to start duplicating P-frames (creates bloom/echo). Leave empty to skip duplication."
    },
    "dup_count": {
        "type": "int",
        "default": 12,
        "prompt": "Number of P-frame duplicates",
        "help": "How many times to duplicate the P-frame. More = stronger bloom. Try 8-20."
    }
}

def clear_screen():
    """Clear terminal screen."""
    os.system('clear' if os.name != 'nt' else 'cls')

def print_header(title: str):
    """Print styled header."""
    print("\n" + "="*70)
    print(f"  {title}")
    print("="*70 + "\n")

def print_section(title: str):
    """Print section divider."""
    print(f"\n{'─'*70}")
    print(f"  {title}")
    print(f"{'─'*70}\n")

def scan_videos(dirpath: str) -> List[str]:
    """Scan directory for video files."""
    if not os.path.isdir(dirpath):
        return []
    videos = []
    for name in sorted(os.listdir(dirpath)):
        p = os.path.join(dirpath, name)
        if os.path.isfile(p) and name.lower().endswith(VIDEO_EXTS):
            videos.append(p)
    return videos

def prompt_choice(prompt: str, choices: List[str], default: Optional[str] = None, show_help: bool = True) -> str:
    """Present menu of choices, return selected."""
    while True:
        print(f"\n{prompt}")
        for i, choice in enumerate(choices, 1):
            marker = " (default)" if default and choice == default else ""
            print(f"  [{i}] {choice}{marker}")
        if default:
            print(f"\nPress ENTER for default ({default}), or enter number:")
        else:
            print("\nEnter number:")

        inp = input("> ").strip()
        if not inp and default:
            return default
        try:
            idx = int(inp) - 1
            if 0 <= idx < len(choices):
                return choices[idx]
            else:
                print(f"❌ Invalid choice. Enter 1-{len(choices)}")
        except KeyboardInterrupt:
            raise
        except ValueError:
            print("❌ Please enter a number")

def prompt_text(prompt: str, default: Optional[str] = None, help_text: Optional[str] = None) -> str:
    """Prompt for text input."""
    if help_text:
        print(f"\n💡 {help_text}")
    if default is not None:
        print(f"\n{prompt} (default: {default})")
    else:
        print(f"\n{prompt}")

    inp = input("> ").strip()
    if not inp and default is not None:
        return default
    return inp

def prompt_int(prompt: str, default: int, help_text: Optional[str] = None) -> int:
    """Prompt for integer input."""
    while True:
        result = prompt_text(prompt, str(default), help_text)
        try:
            return int(result)
        except KeyboardInterrupt:
            raise
        except ValueError:
            print("❌ Please enter a valid number")

def prompt_float(prompt: str, default: float, help_text: Optional[str] = None) -> float:
    """Prompt for float input."""
    while True:
        result = prompt_text(prompt, str(default), help_text)
        try:
            return float(result)
        except KeyboardInterrupt:
            raise
        except ValueError:
            print("❌ Please enter a valid number")

def prompt_bool(prompt: str, default: bool = False) -> bool:
    """Prompt for yes/no."""
    default_str = "y" if default else "n"
    result = prompt_text(f"{prompt} (y/n)", default_str)
    return result.lower() in ('y', 'yes', 'true', '1')

def select_algorithm() -> str:
    """Interactive algorithm selection with categories."""
    clear_screen()
    print_header("d4t4m0sh Interactive Wizard")

    # Group by category
    categories = {
        "analysis": [],
        "basic": [],
        "advanced": [],
        "creative": [],
        "avidemux": [],
        "transitions": []
    }

    for algo_id, info in ALGORITHM_INFO.items():
        categories[info["category"]].append((algo_id, info))

    print("📋 Available Algorithms (grouped by category):\n")

    all_choices = []
    category_names = {
        "analysis": "🔍 Analysis Tools",
        "basic": "⚡ Basic Datamosh",
        "advanced": "🎯 Advanced Control",
        "creative": "🎨 Creative Effects",
        "avidemux": "💥 Avidemux-Style (Strongest)",
        "transitions": "🌊 Transitions (P-Cascade)"
    }

    idx = 1
    for cat in ["analysis", "basic", "advanced", "creative", "avidemux", "transitions"]:
        print(f"\n{category_names[cat]}:")
        for algo_id, info in categories[cat]:
            print(f"  [{idx}] {info['name']}")
            print(f"      {info['desc']}")
            print(f"      💭 {info['use_case']}")
            all_choices.append(algo_id)
            idx += 1

    print("\n" + "─"*70)
    while True:
        try:
            choice_idx = int(input("\nSelect algorithm number: ").strip()) - 1
            if 0 <= choice_idx < len(all_choices):
                selected = all_choices[choice_idx]
                print(f"\n✓ Selected: {ALGORITHM_INFO[selected]['name']}")
                return selected
            else:
                print(f"❌ Invalid choice. Enter 1-{len(all_choices)}")
        except KeyboardInterrupt:
            raise
        except ValueError:
            print("❌ Please enter a valid number")

def select_files(algo_info: Dict[str, Any], videosrc: str = "videosrc") -> List[str]:
    """Select input files based on algorithm requirements."""
    print_section("Input Selection")

    videos = scan_videos(videosrc)
    if not videos:
        print(f"❌ No videos found in '{videosrc}/' directory.")
        print(f"   Place video files ({', '.join(VIDEO_EXTS)}) in that folder and try again.")
        sys.exit(1)

    print(f"Found {len(videos)} video(s) in {videosrc}/:\n")
    for i, v in enumerate(videos, 1):
        size_mb = os.path.getsize(v) / (1024*1024)
        print(f"  [{i}] {os.path.basename(v)} ({size_mb:.1f} MB)")

    if algo_info["inputs"] == "single":
        print("\n💡 This algorithm processes ONE video file.")
        while True:
            try:
                choice = input("\nSelect file number (or ENTER for #1): ").strip()
                idx = 0 if not choice else int(choice) - 1
                if 0 <= idx < len(videos):
                    selected = videos[idx]
                    print(f"✓ Selected: {os.path.basename(selected)}")
                    return [selected]
                print(f"❌ Invalid. Enter 1-{len(videos)}")
            except KeyboardInterrupt:
                raise
            except ValueError:
                print("❌ Please enter a valid number")
    elif algo_info["inputs"] == "two":
        print("\n💡 This algorithm requires EXACTLY TWO video files (clip A → clip B).")

        # Select first clip
        while True:
            try:
                choice = input("\nSelect FIRST clip (A) number: ").strip()
                idx_a = int(choice) - 1
                if 0 <= idx_a < len(videos):
                    break
                print(f"❌ Invalid. Enter 1-{len(videos)}")
            except KeyboardInterrupt:
                raise
            except ValueError:
                print("❌ Please enter a valid number")

        # Select second clip
        while True:
            try:
                choice = input("Select SECOND clip (B) number: ").strip()
                idx_b = int(choice) - 1
                if 0 <= idx_b < len(videos):
                    if idx_b == idx_a:
                        print("⚠️  Warning: Using same clip twice. Continue? (y/n)")
                        if input("> ").strip().lower() not in ('y', 'yes'):
                            continue
                    break
                print(f"❌ Invalid. Enter 1-{len(videos)}")
            except KeyboardInterrupt:
                raise
            except ValueError:
                print("❌ Please enter a valid number")

        selected = [videos[idx_a], videos[idx_b]]
        print(f"✓ Selected transition: {os.path.basename(selected[0])} → {os.path.basename(selected[1])}")
        return selected
    else:
        print("\n💡 This algorithm can process MULTIPLE videos.")
        print("   Enter numbers separated by commas in desired order (e.g. 3,1,2)")
        print("   Or press ENTER to use all files in current order.")

        while True:
            choice = input("\nSelect files: ").strip()
            if not choice:
                print(f"✓ Selected all {len(videos)} files in order")
                return videos

            try:
                indices = [int(x.strip()) - 1 for x in choice.split(",")]
                if all(0 <= i < len(videos) for i in indices):
                    selected = [videos[i] for i in indices]
                    print(f"✓ Selected {len(selected)} file(s):")
                    for f in selected:
                        print(f"  • {os.path.basename(f)}")
                    return selected
                print(f"❌ Invalid indices. Use 1-{len(videos)}")
            except KeyboardInterrupt:
                raise
            except ValueError:
                print("❌ Invalid format. Use comma-separated numbers (e.g. 1,3,2)")

def configure_pass_params(pass_num: int) -> Dict[str, Any]:
    """Configure parameters for a single mosh pass."""
    print(f"\n{'─'*70}")
    print(f"  Pass {pass_num} Configuration")
    print(f"{'─'*70}\n")

    pass_config = {}

    # I-frame drop window
    print("💡 I-frame removal creates smear effects")
    has_drop = prompt_bool("Configure I-frame removal for this pass?", default=True)

    if has_drop:
        while True:
            drop_start = prompt_float("I-frame drop start (seconds)", default=2.0,
                                     help_text="Start time to remove keyframes")
            drop_end = prompt_float("I-frame drop end (seconds)", default=4.0,
                                   help_text="End time for keyframe removal")
            if drop_end > drop_start:
                pass_config["drop_start"] = drop_start
                pass_config["drop_end"] = drop_end
                break
            print("❌ Drop end must be greater than drop start")

    # P-frame duplication
    print("\n💡 P-frame duplication creates bloom/echo effects")
    has_dup = prompt_bool("Configure P-frame duplication for this pass?", default=False)

    if has_dup:
        dup_at = prompt_float("P-frame duplication start time (seconds)", default=3.0,
                             help_text="When to start duplicating P-frames")
        dup_count = prompt_int("Number of P-frame duplicates", default=12,
                              help_text="More = stronger bloom. Try 8-20")
        pass_config["dup_at"] = dup_at
        pass_config["dup_count"] = dup_count

    # Validate at least one operation
    if not has_drop and not has_dup:
        print("⚠️  Warning: At least one operation (drop or dup) required. Enabling I-frame removal.")
        drop_start = prompt_float("I-frame drop start (seconds)", default=2.0)
        drop_end = prompt_float("I-frame drop end (seconds)", default=4.0)
        pass_config["drop_start"] = drop_start
        pass_config["drop_end"] = drop_end

    return pass_config


def configure_options(algo_id: str, algo_info: Dict[str, Any]) -> Dict[str, Any]:
    """Interactive configuration of algorithm options."""
    print_section("Configuration")

    options = algo_info["options"]
    if not options:
        print("💡 This algorithm has no configurable options.")
        return {}

    config = {}

    print(f"Configure {algo_info['name']}:\n")

    for opt_name in options:
        if opt_name not in OPTION_INFO:
            continue

        opt = OPTION_INFO[opt_name]

        if opt["type"] == "int":
            config[opt_name] = prompt_int(opt["prompt"], opt["default"], opt["help"])
        elif opt["type"] == "float":
            config[opt_name] = prompt_float(opt["prompt"], opt["default"], opt["help"])
        elif opt["type"] == "bool":
            config[opt_name] = prompt_bool(opt["prompt"], opt["default"])
        elif opt["type"] == "str":
            config[opt_name] = prompt_text(opt["prompt"], opt["default"], opt["help"])
        elif opt["type"] == "choice":
            config[opt_name] = prompt_choice(opt["prompt"], opt["choices"], opt["default"], True)
        elif opt["type"] == "range":
            result = prompt_text(opt["prompt"] + " (or ENTER to skip)", "skip", opt["help"])
            if result != "skip":
                config[opt_name] = result
        elif opt["type"] == "file":
            result = prompt_text(opt["prompt"] + " (or ENTER to skip)", "", opt["help"])
            if result:
                config[opt_name] = result

    return config

def select_output(algo_info: Dict[str, Any], input_files: List[str]) -> str:
    """Select output file path."""
    print_section("Output")

    # Suggest output based on first input
    first_input = os.path.basename(input_files[0])
    root, _ = os.path.splitext(first_input)

    suggested_ext = ".avi" if "avidemux" in algo_info["category"] else ".mp4"
    if algo_info["outputs"] == ".csv":
        suggested_ext = ".csv"

    suggested = f"{root}.moshed{suggested_ext}"

    print(f"💡 Recommended extension: {algo_info['outputs']}")
    output = prompt_text(f"Output filename", suggested,
                        "Save to current directory. Use .avi for strongest artifacts, .mp4 for compatibility.")

    return output

def build_command(algo_id: str, input_files: List[str], output: str, config: Dict[str, Any]) -> List[str]:
    """Build the command from configuration."""
    # Special handling for mosh tool
    if algo_id == "mosh":
        cmd = ["python3", "mosh.py"]
        cmd.extend(["--a", input_files[0]])
        cmd.extend(["--b", input_files[1]])

        # Add mosh-specific options
        for key, value in config.items():
            if value is not None and value != "":
                # Convert option names
                if key == "mosh_q":
                    cmd.extend(["--q", str(value)])
                elif key == "gop_len":
                    cmd.extend(["--gop-len", str(value)])
                elif key == "no_iframe_window":
                    cmd.extend(["--no-iframe-window", str(value)])
                elif key == "repeat_boost":
                    cmd.extend(["--repeat-boost", str(value)])
                elif key == "repeat_times":
                    cmd.extend(["--repeat-times", str(value)])
                elif key == "postcut":
                    cmd.extend(["--postcut", str(value)])
                elif key == "verbose" and value:
                    cmd.append("-v")
                else:
                    cmd.extend([f"--{key}", str(value)])

        return cmd

    # Special handling for mosh_zoom_oneclip tool
    if algo_id == "mosh_zoom_oneclip":
        cmd = ["python3", "mosh_zoom_oneclip.py"]
        cmd.extend(["--in", input_files[0]])
        cmd.extend(["--out", output])

        # Add zoom-specific options
        for key, value in config.items():
            if value is not None and value != "" and value is not False:
                if key == "zoom_q":
                    cmd.extend(["--q", str(value)])
                elif key == "zoom_t":
                    cmd.extend(["--t", str(value)])
                elif key == "zoom_dur":
                    cmd.extend(["--zoom-dur", str(value)])
                elif key == "zoom_tail":
                    cmd.extend(["--tail", str(value)])
                elif key == "zoom_direction":
                    cmd.extend(["--zoom-direction", str(value)])
                elif key == "deliver_crf":
                    cmd.extend(["--deliver-crf", str(value)])
                elif key == "verbose" and value:
                    cmd.append("-v")
                else:
                    cmd.extend([f"--{key}", str(value)])

        return cmd

    # Special handling for mosh_h264 tool
    if algo_id == "mosh_h264":
        cmd = ["python3", "mosh_h264.py"]
        cmd.extend(["--a", input_files[0]])
        cmd.extend(["--b", input_files[1]])

        # Add h264-specific options
        for key, value in config.items():
            if value is not None and value != "" and value is not False:
                if key == "h264_crf":
                    cmd.extend(["--crf", str(value)])
                elif key == "gop_len":
                    cmd.extend(["--gop-len", str(value)])
                elif key == "no_iframe_window":
                    cmd.extend(["--no-iframe-window", str(value)])
                elif key == "repeat_boost":
                    cmd.extend(["--repeat-boost", str(value)])
                elif key == "repeat_times":
                    cmd.extend(["--repeat-times", str(value)])
                elif key == "postcut":
                    cmd.extend(["--postcut", str(value)])
                elif key == "verbose" and value:
                    cmd.append("-v")
                else:
                    cmd.extend([f"--{key}", str(value)])

        return cmd

    # Special handling for ts_mosh tool
    if algo_id == "ts_mosh":
        cmd = ["python3", "ts_mosh.py"]
        cmd.extend(["--in", input_files[0]])
        cmd.extend(["--out", output])

        # Add ts_mosh-specific options
        for key, value in config.items():
            if value is not None and value != "" and value is not False:
                if key == "ts_format":
                    cmd.extend(["--format", str(value)])
                elif key == "vbitrate":
                    cmd.extend(["--vbitrate", str(value)])
                elif key == "keyint":
                    cmd.extend(["--keyint", str(value)])
                elif key == "start_ms":
                    cmd.extend(["--start-ms", str(value)])
                elif key == "duration_ms":
                    cmd.extend(["--duration-ms", str(value)])
                elif key == "ts_pps":
                    cmd.extend(["--pps", str(value)])
                elif key == "ts_pid" and value:
                    cmd.extend(["--pid", str(value)])
                elif key == "xvid_q":
                    cmd.extend(["--xvid-q", str(value)])
                elif key == "fps":
                    cmd.extend(["--fps", str(value)])
                elif key == "verbose" and value:
                    cmd.append("-v")
                else:
                    cmd.extend([f"--{key}", str(value)])

        return cmd

    # Special handling for aviglitch_mosh tool
    if algo_id == "aviglitch_mosh":
        cmd = ["python3", "aviglitch_mosh.py"]
        cmd.extend(["--in", input_files[0]])
        cmd.extend(["--out", output])

        # Add aviglitch-specific options
        for key, value in config.items():
            if value is not None and value != "" and value is not False:
                if key == "aviglitch_prep" and value:
                    cmd.append("--prep")
                elif key == "prep_q":
                    cmd.extend(["--prep-q", str(value)])
                elif key == "prep_gop_ag":
                    cmd.extend(["--prep-gop", str(value)])
                elif key == "prep_fps":
                    cmd.extend(["--prep-fps", str(value)])
                elif key == "drop_start":
                    cmd.extend(["--drop-start", str(value)])
                elif key == "drop_end":
                    cmd.extend(["--drop-end", str(value)])
                elif key == "dup_at":
                    cmd.extend(["--dup-at", str(value)])
                elif key == "dup_count":
                    cmd.extend(["--dup-count", str(value)])
                elif key == "verbose" and value:
                    cmd.append("-v")
                else:
                    cmd.extend([f"--{key}", str(value)])

        return cmd

    # Standard main.py command
    cmd = ["python3", "main.py", "-a", algo_id]

    # Add inputs
    if len(input_files) == 1:
        cmd.extend(["-f", input_files[0]])
    else:
        cmd.extend(["-f", ",".join(input_files)])

    # Add output
    cmd.extend(["-o", output])

    # Add options
    for key, value in config.items():
        # Skip None, empty strings, and False boolean values
        if value is None or value == "" or value is False:
            continue

        if key == "verbose" and value:
            cmd.append("-v")
        elif key == "postcut_rand":
            # Special case: postcut_rand uses hyphen in main.py
            cmd.extend(["--postcut-rand", str(value)])
        elif key == "kb_mode":
            cmd.extend(["--kb", str(value)])
        else:
            # Keep underscores - main.py uses underscores for most args
            cmd.extend([f"--{key}", str(value)])

    return cmd

def execute_multipass_aviglitch(input_files: List[str], base_config: Dict[str, Any], passes: List[Dict[str, Any]], final_output: str):
    """Execute multiple aviglitch_mosh passes in sequence."""
    print("\n🚀 Starting multi-pass processing...\n")

    # Generate intermediate filenames
    root, ext = os.path.splitext(final_output)
    intermediate_files = []

    current_input = input_files[0]
    success = True

    for i, pass_config in enumerate(passes, 1):
        # Determine output path
        if i == len(passes):
            # Final pass uses user-specified output name
            pass_output = final_output
        else:
            # Intermediate passes use _passN naming
            pass_output = f"{root}_pass{i}{ext}"
            intermediate_files.append(pass_output)

        print(f"{'─'*70}")
        print(f"  Pass {i}/{len(passes)}: {os.path.basename(current_input)} → {os.path.basename(pass_output)}")
        print(f"{'─'*70}\n")

        # Build command for this pass
        cmd = ["python3", "aviglitch_mosh.py"]
        cmd.extend(["--in", current_input])
        cmd.extend(["--out", pass_output])

        # Only apply prep on first pass
        if i == 1 and base_config.get("aviglitch_prep"):
            cmd.append("--prep")
            if "prep_q" in base_config:
                cmd.extend(["--prep-q", str(base_config["prep_q"])])
            if "prep_gop_ag" in base_config:
                cmd.extend(["--prep-gop", str(base_config["prep_gop_ag"])])
            if "prep_fps" in base_config:
                cmd.extend(["--prep-fps", str(base_config["prep_fps"])])

        # Add pass-specific mosh params
        if "drop_start" in pass_config:
            cmd.extend(["--drop-start", str(pass_config["drop_start"])])
            cmd.extend(["--drop-end", str(pass_config["drop_end"])])
        if "dup_at" in pass_config:
            cmd.extend(["--dup-at", str(pass_config["dup_at"])])
            cmd.extend(["--dup-count", str(pass_config["dup_count"])])

        # Add verbose if configured
        if base_config.get("verbose"):
            cmd.append("-v")

        print(f"Command: {' '.join(cmd)}\n")

        # Execute pass
        try:
            result = subprocess.run(cmd)
        except KeyboardInterrupt:
            print(f"\n❌ Pass {i} interrupted by user")
            raise

        if result.returncode != 0:
            print(f"\n❌ Pass {i} failed with exit code {result.returncode}")
            success = False
            break

        print(f"\n✓ Pass {i} completed: {pass_output}\n")

        # Next pass uses this output as input
        current_input = pass_output

    if success:
        print(f"\n{'='*70}")
        print(f"✅ All {len(passes)} passes completed successfully!")
        print(f"{'='*70}")
        print(f"\nFinal output: {final_output}")

        if intermediate_files:
            print(f"\nIntermediate files:")
            for f in intermediate_files:
                print(f"  • {f}")

            cleanup = prompt_bool("\nDelete intermediate files?", default=False)
            if cleanup:
                for f in intermediate_files:
                    if os.path.exists(f):
                        os.remove(f)
                        print(f"  Deleted: {f}")
    else:
        sys.exit(1)


def main():
    """Main wizard flow."""
    try:
        # Step 1: Select algorithm
        algo_id = select_algorithm()
        algo_info = ALGORITHM_INFO[algo_id]

        # Step 2: Select input files
        input_files = select_files(algo_info)

        # Step 3: Configure options
        config = configure_options(algo_id, algo_info)

        # Step 3.5: Check for multi-pass (aviglitch_mosh only)
        multipass_enabled = False
        pass_configs = []

        if algo_id == "aviglitch_mosh":
            print_section("Multi-Pass Configuration")
            print("💡 Multi-pass mode applies multiple rounds of moshing to the same clip.")
            print("   Each pass can have different I-frame drop and P-frame duplication settings.")
            print("   Conversion/prep settings are applied once on the first pass.\n")

            multipass_enabled = prompt_bool("Enable multi-pass mode?", default=False)

            if multipass_enabled:
                while True:
                    num_passes = prompt_int("Number of passes", default=2,
                                           help_text="How many times to mosh this clip (2-5 recommended)")
                    if 2 <= num_passes <= 10:
                        break
                    print("❌ Please enter 2-10 passes")

                # Collect configuration for each pass
                for i in range(1, num_passes + 1):
                    pass_config = configure_pass_params(i)
                    pass_configs.append(pass_config)

        # Step 4: Select output (skip for mosh/mosh_h264 - they have fixed output names)
        if algo_id in ("mosh", "mosh_h264"):
            output = None
        else:
            output = select_output(algo_info, input_files)

        # Step 5: Review and confirm
        clear_screen()
        print_header("Configuration Summary")

        print(f"Algorithm: {algo_info['name']}")
        print(f"Input(s):  {len(input_files)} file(s)")
        for f in input_files:
            print(f"           • {os.path.basename(f)}")

        if multipass_enabled:
            print(f"Mode:      Multi-pass ({len(pass_configs)} passes)")
            print(f"Output:    {output}")

            if config:
                print(f"\nBase Options (applied to all passes):")
                for key, value in config.items():
                    if key not in ("drop_start", "drop_end", "dup_at", "dup_count"):
                        print(f"  {key}: {value}")

            print(f"\nPass Configurations:")
            for i, pconf in enumerate(pass_configs, 1):
                print(f"  Pass {i}:")
                for key, value in pconf.items():
                    print(f"    {key}: {value}")
        else:
            if output:
                print(f"Output:    {output}")
            elif algo_id == "mosh":
                print(f"Outputs:   out_longgop.avi, mosh_core.avi, mosh_final.avi, mosh_final.mp4")
            elif algo_id == "mosh_h264":
                print(f"Outputs:   out_longgop_h264.mp4, mosh_core_h264.mp4, mosh_final_h264.mp4")

            if config:
                print(f"\nOptions:")
                for key, value in config.items():
                    print(f"  {key}: {value}")

        # Build and execute command(s)
        if multipass_enabled:
            print(f"\n{'─'*70}")
            print(f"Multi-pass execution: {len(pass_configs)} sequential operations")
            print(f"{'─'*70}\n")

            # Confirm
            if not prompt_bool("Execute now?", True):
                print("\n❌ Cancelled.")
                return

            # Execute multi-pass
            execute_multipass_aviglitch(input_files, config, pass_configs, output)
        else:
            # Build single command
            cmd = build_command(algo_id, input_files, output, config)

            print(f"\n{'─'*70}")
            print("Command to execute:")
            print(f"{'─'*70}")
            print(" ".join(cmd))
            print(f"{'─'*70}\n")

            # Confirm
            if not prompt_bool("Execute now?", True):
                print("\n❌ Cancelled. You can run the command above manually later.")
                return

            # Execute
            print("\n🚀 Starting processing...\n")
            try:
                result = subprocess.run(cmd)
            except KeyboardInterrupt:
                print("\n❌ Processing interrupted by user")
                raise

            if result.returncode == 0:
                if output:
                    print(f"\n✅ Success! Output saved to: {output}")
                else:
                    print(f"\n✅ Success! Outputs created in working directory.")
            else:
                print(f"\n❌ Processing failed with exit code {result.returncode}")
                sys.exit(result.returncode)

    except KeyboardInterrupt:
        print("\n\n❌ Cancelled by user.")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
