#!/usr/bin/env bash
set -euo pipefail

# Remove all I-frames except the first (frame 0)
# Requires: ffmpeg (brew install ffmpeg)

usage() {
  cat <<'USAGE'
Usage:
  drop_i_frames.sh -i INPUT -o OUTPUT [options]

Options:
  -i INPUT           Input video file (any format ffmpeg can read)
  -o OUTPUT          Output path (.avi recommended for strongest artifacts, or .mp4)
  -g GOP             GOP size for intermediate (default: 9999 = huge)
  -c CODEC           Final encoder when OUTPUT is .mp4/.mov/.m4v
                     (default on macOS: h264_videotoolbox, otherwise: libx264)
  -k                 Keep audio in final (re-encode, -shortest). Default: drop audio (-an)
  -q QSCALE          Quality for MPEG-4 intermediate and moshed AVI (lower = better, default 3)
  -v                 Verbose ffmpeg logs
  -h                 Help

Notes:
  * Audio is dropped by default to avoid A/V drift after frame removal.
    Use -k to keep audio (will be re-encoded and trimmed to video length).
  * For strongest moshing, keep OUTPUT as .avi. Re-encoding to .mp4 softens artifacts a bit.
USAGE
}

# --- defaults ---
GOP=9999
KEEP_AUDIO=0
QSCALE=3
VERBOSE=0
INPUT=""
OUTPUT=""
CODEC_DEFAULT="libx264"

# Prefer macOS hardware encoder if available
if [[ "$(uname -s)" == "Darwin" ]]; then
  CODEC_DEFAULT="h264_videotoolbox"
fi
CODEC="$CODEC_DEFAULT"

while getopts ":i:o:g:c:kq:vh" opt; do
  case "$opt" in
    i) INPUT="$OPTARG" ;;
    o) OUTPUT="$OPTARG" ;;
    g) GOP="$OPTARG" ;;
    c) CODEC="$OPTARG" ;;
    k) KEEP_AUDIO=1 ;;
    q) QSCALE="$OPTARG" ;;
    v) VERBOSE=1 ;;
    h) usage; exit 0 ;;
    \?) echo "Unknown option: -$OPTARG" >&2; usage; exit 2 ;;
    :)  echo "Option -$OPTARG requires an argument." >&2; usage; exit 2 ;;
  esac
done

[[ -z "$INPUT" || -z "$OUTPUT" ]] && { usage; exit 2; }
command -v ffmpeg >/dev/null 2>&1 || { echo "ffmpeg not found. Install via Homebrew: brew install ffmpeg"; exit 1; }
[[ -r "$INPUT" ]] || { echo "Input not readable: $INPUT" >&2; exit 1; }

loglvl=error
[[ $VERBOSE -eq 1 ]] && loglvl=info

tmpdir="$(mktemp -d -t datamosh_dropI.XXXXXX)"
trap 'rm -rf "$tmpdir"' EXIT

stage1="$tmpdir"/stage1_intermediate.avi
stage2="$tmpdir"/stage2_moshed.avi

echo "==> Stage 1: Make mosh-friendly intermediate (AVI + MPEG-4, huge GOP, no B-frames)"
ffmpeg -hide_banner -loglevel "$loglvl" -y \
  -i "$INPUT" \
  -an \
  -c:v mpeg4 -qscale:v "$QSCALE" \
  -g "$GOP" -bf 0 -sc_threshold 0 \
  -pix_fmt yuv420p \
  "$stage1"

echo "==> Stage 2: Drop all I-frames except the first"
# keep n==0 OR not I
select_expr="eq(n\,0)+not(eq(pict_type\,I))"
ffmpeg -hide_banner -loglevel "$loglvl" -y \
  -i "$stage1" \
  -vf "select='${select_expr}',setpts=N/FRAME_RATE/TB" \
  -an \
  -c:v mpeg4 -qscale:v "$QSCALE" \
  -pix_fmt yuv420p \
  "$stage2"

ext="${OUTPUT##*.}"
shopt -s nocasematch
case "$ext" in
  avi)
    echo "==> Writing AVI (no further re-encode)"
    mkdir -p "$(dirname "$OUTPUT")"
    cp "$stage2" "$OUTPUT"
    ;;
  mp4|mov|m4v)
    echo "==> Final transcode to $ext using $CODEC"
    if [[ $KEEP_AUDIO -eq 1 ]]; then
      ffmpeg -hide_banner -loglevel "$loglvl" -y \
        -i "$stage2" -i "$INPUT" \
        -map 0:v:0 -map 1:a:0? \
        -c:v "$CODEC" -pix_fmt yuv420p \
        -c:a aac -b:a 192k \
        -movflags +faststart -shortest \
        "$OUTPUT"
    else
      ffmpeg -hide_banner -loglevel "$loglvl" -y \
        -i "$stage2" \
        -an \
        -c:v "$CODEC" -pix_fmt yuv420p \
        -movflags +faststart \
        "$OUTPUT"
    fi
    ;;
  *)
    echo "==> Unknown output extension: .$ext"
    echo "    Tip: use .avi for raw mosh look or .mp4 for broad compatibility."
    exit 2
    ;;
esac
shopt -u nocasematch

echo "✅ Done: $OUTPUT"
