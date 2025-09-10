#!/usr/bin/env bash
set -euo pipefail

# Convert videos to Xvid/AVI (mosh-friendly)
# - Forces even width/height, constant framerate, no B-frames
# - Long GOP (default 600; you can lower if your build complains)
# - Audio: MP3 by default (AVI-friendly), or disable/copy/PCM
#
# Examples:
#   ./convert_to_xvid.sh -i videosrc -o xvid_out
#   ./convert_to_xvid.sh -i input.mov -o xvid_out -q 6 -r 30 -g 600 -a none
#   ./convert_to_xvid.sh -i videosrc -o xvid_out -s 1920x1080

usage() {
  cat <<'USAGE'
Usage:
  convert_to_xvid.sh -i INPUT [-o OUTDIR] [options]

INPUT:
  -i INPUT    Path to a file OR a directory to batch-convert (non-recursive).

OUTPUT:
  -o OUTDIR   Output directory (default: ./xvid_out)

VIDEO OPTIONS:
  -q Q        Quality (quantizer) for Xvid/MPEG-4; lower=better, higher=blockier (default: 3)
  -g GOP      GOP/keyframe interval (default: 600). Some builds cap at 300/600.
  -r FPS      Output FPS (constant) (default: 30)
  -s WxH      Force output size, e.g. 1920x1080 (default: keep source size, rounded to even)

AUDIO OPTIONS:
  -a MODE     Audio mode: none | mp3 | copy | pcm  (default: mp3 at 192k)

MISC:
  -v          Verbose FFmpeg logs
  -h          Help

Notes:
  * Container is AVI; codec is libxvid when available, otherwise MPEG-4 with XVID FourCC.
  * We enforce: even dimensions, CFR, -bf 0, -sc_threshold 0, yuv420p.
USAGE
}

# Defaults
INPUT=""
OUTDIR="xvid_out"
Q=3
GOP=600
FPS=30
SIZE=""          # e.g. "1920x1080"
AUDIO_MODE="mp3" # none|mp3|copy|pcm
VERBOSE=0

# Parse args
while getopts ":i:o:q:g:r:s:a:vh" opt; do
  case "$opt" in
    i) INPUT="$OPTARG" ;;
    o) OUTDIR="$OPTARG" ;;
    q) Q="$OPTARG" ;;
    g) GOP="$OPTARG" ;;
    r) FPS="$OPTARG" ;;
    s) SIZE="$OPTARG" ;;
    a) AUDIO_MODE="$OPTARG" ;;
    v) VERBOSE=1 ;;
    h) usage; exit 0 ;;
    \?) echo "Unknown option -$OPTARG" >&2; usage; exit 2 ;;
    :)  echo "Option -$OPTARG requires an argument." >&2; usage; exit 2 ;;
  esac
done

[[ -z "$INPUT" ]] && { echo "Missing -i INPUT" >&2; usage; exit 2; }
command -v ffmpeg >/dev/null 2>&1 || { echo "ffmpeg not found. Install via Homebrew: brew install ffmpeg" >&2; exit 1; }

# Determine codec: prefer libxvid if encoder exists, else mpeg4 + XVID tag
CODEC="mpeg4"
if ffmpeg -hide_banner -v error -h encoder=libxvid >/dev/null 2>&1; then
  CODEC="libxvid"
fi

loglvl="error"
[[ $VERBOSE -eq 1 ]] && loglvl="info"

mkdir -p "$OUTDIR"

# Build audio args
build_audio_args() {
  case "$AUDIO_MODE" in
    none) echo "-an" ;;
    copy) echo "-c:a copy" ;;
    pcm)  echo "-c:a pcm_s16le" ;;
    mp3)  echo "-c:a libmp3lame -b:a 192k" ;;
    *)    echo "Invalid audio mode: $AUDIO_MODE" >&2; exit 2 ;;
  esac
}

# Convert single file
convert_one() {
  local in="$1"
  local base ext out
  base="$(basename "$in")"
  ext="${base##*.}"
  base="${base%.*}"
  out="${OUTDIR}/${base}.xvid.avi"

  # Scale filter: ensure even dims, optional target size, set fps
  local vf="scale=trunc(iw/2)*2:trunc(ih/2)*2"
  if [[ -n "$SIZE" ]]; then
    vf="${vf},scale=${SIZE}"
  fi
  vf="${vf},fps=${FPS}"

  # Video encoder args
  local vargs=()
  if [[ "$CODEC" == "libxvid" ]]; then
    vargs=(-c:v libxvid -qscale:v "$Q" -g "$GOP" -bf 0 -sc_threshold 0 -pix_fmt yuv420p)
  else
    # mpeg4 fallback with XVID FourCC
    # (Some builds clamp -g; if you see 'keyframe interval too large', lower -g to 300)
    vargs=(-c:v mpeg4 -vtag XVID -qscale:v "$Q" -g "$GOP" -bf 0 -sc_threshold 0 -pix_fmt yuv420p)
  fi

  # Audio args
  IFS=' ' read -r -a aargs <<< "$(build_audio_args)"

  echo "==> Converting: $in"
  ffmpeg -hide_banner -loglevel "$loglvl" -y \
    -i "$in" \
    -vf "$vf" \
    -r "$FPS" -vsync cfr \
    "${vargs[@]}" \
    "${aargs[@]}" \
    "$out"
  echo "OK: $out"
}

# Collect inputs
shopt -s nullglob
files=()
if [[ -d "$INPUT" ]]; then
  for f in "$INPUT"/*.{mp4,MP4,mov,MOV,m4v,M4V,avi,AVI,mkv,MKV,webm,WEBM}; do
    [[ -e "$f" ]] && files+=("$f")
  done
  if [[ ${#files[@]} -eq 0 ]]; then
    echo "No videos found in directory: $INPUT" >&2; exit 1
  fi
elif [[ -f "$INPUT" ]]; then
  files+=("$INPUT")
else
  echo "Input not found: $INPUT" >&2; exit 1
fi

# Convert all
for f in "${files[@]}"; do
  convert_one "$f"
done
