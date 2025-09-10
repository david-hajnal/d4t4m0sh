#!/usr/bin/env bash
set -euo pipefail

# Multi-input datamosh:
#  - For the FIRST input: keep frame 0, drop all other I-frames
#  - For SUBSEQUENT inputs: drop ALL I-frames
#  - Concatenate the results
#  - Optionally transcode to MP4/MOV with desired GOP/codec
#
# Requires: ffmpeg (brew install ffmpeg)

usage() {
  cat <<'USAGE'
Usage:
  multi_drop_concat.sh -o OUTPUT [options] -- INPUT1 [INPUT2 ... INPUTN]

Options:
  -o OUTPUT        Output file (.avi recommended for strongest artifacts, or .mp4/.mov)
  -g GOP           GOP size for intermediate and/or final (default: 9999)
  -q QSCALE        Quality for MPEG-4 intermediates and concat (lower=better; default: 3)
  -c CODEC         Final encoder when OUTPUT is .mp4/.mov/.m4v
                   (default macOS: h264_videotoolbox; else libx264)
  -r FPS           Force a target framerate for ALL intermediates (ensures concat-compat)
  -v               Verbose ffmpeg logs
  -h               Help

Notes:
  * Audio is dropped to avoid A/V drift when removing frames and concatenating.
  * For safest concat, make sure all inputs share resolution; if not, pre-scale them
    to a common size or add a step to rescale (we can extend this script if needed).
USAGE
}

# --- defaults ---
GOP=9999
QSCALE=3
VERBOSE=0
FPS=""
OUTPUT=""
CODEC_DEFAULT="libx264"
[[ "$(uname -s)" == "Darwin" ]] && CODEC_DEFAULT="h264_videotoolbox"
CODEC="$CODEC_DEFAULT"

# --- parse ---
ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o) OUTPUT="${2:-}"; shift 2 ;;
    -g) GOP="${2:-}"; shift 2 ;;
    -q) QSCALE="${2:-}"; shift 2 ;;
    -c) CODEC="${2:-}"; shift 2 ;;
    -r) FPS="${2:-}"; shift 2 ;;
    -v) VERBOSE=1; shift ;;
    -h) usage; exit 0 ;;
    --) shift; ARGS+=("$@"); break ;;
    -*) echo "Unknown option: $1" >&2; usage; exit 2 ;;
    *)  ARGS+=("$1"); shift ;;
  esac
done

[[ -z "${OUTPUT}" ]] && { echo "Missing -o OUTPUT" >&2; usage; exit 2; }
[[ ${#ARGS[@]} -lt 1 ]] && { echo "Provide at least one input after --" >&2; usage; exit 2; }
for f in "${ARGS[@]}"; do [[ -r "$f" ]] || { echo "Input not readable: $f" >&2; exit 1; }; done
command -v ffmpeg >/dev/null || { echo "ffmpeg not found. brew install ffmpeg"; exit 1; }

loglvl=error
[[ $VERBOSE -eq 1 ]] && loglvl=info

tmpdir="$(mktemp -d -t datamosh_multi.XXXXXX)"
trap 'rm -rf "$tmpdir"' EXIT

# Stage outputs
stg1=()   # per-input: mosh-friendly AVI
stg2=()   # per-input: I-frames dropped (except very first overall)
idx=0
for inpath in "${ARGS[@]}"; do
  b="$(printf "clip_%03d" "$idx")"
  s1="$tmpdir/${b}_stage1.avi"
  s2="$tmpdir/${b}_stage2.avi"
  stg1+=("$s1"); stg2+=("$s2")

  echo "==> [$idx] Stage 1: ${inpath} -> mosh-friendly AVI (mpeg4, huge GOP, no B-frames)"
  if [[ -n "$FPS" ]]; then
    ffmpeg -hide_banner -loglevel "$loglvl" -y \
      -i "$inpath" -an \
      -r "$FPS" \
      -c:v mpeg4 -qscale:v "$QSCALE" \
      -g "$GOP" -bf 0 -sc_threshold 0 \
      -pix_fmt yuv420p \
      "$s1"
  else
    ffmpeg -hide_banner -loglevel "$loglvl" -y \
      -i "$inpath" -an \
      -c:v mpeg4 -qscale:v "$QSCALE" \
      -g "$GOP" -bf 0 -sc_threshold 0 \
      -pix_fmt yuv420p \
      "$s1"
  fi

  echo "==> [$idx] Stage 2: drop I-frames"
  if [[ $idx -eq 0 ]]; then
    # Keep first frame overall (n==0), drop other I's
    sel="eq(n\\,0)+not(eq(pict_type\\,I))"
  else
    # For all subsequent clips, drop ALL I's
    sel="not(eq(pict_type\\,I))"
  fi
  if [[ -n "$FPS" ]]; then
    ffmpeg -hide_banner -loglevel "$loglvl" -y \
      -i "$s1" \
      -vf "select='${sel}',setpts=N/FRAME_RATE/TB" \
      -an -r "$FPS" \
      -c:v mpeg4 -qscale:v "$QSCALE" -pix_fmt yuv420p \
      "$s2"
  else
    ffmpeg -hide_banner -loglevel "$loglvl" -y \
      -i "$s1" \
      -vf "select='${sel}',setpts=N/FRAME_RATE/TB" \
      -an \
      -c:v mpeg4 -qscale:v "$QSCALE" -pix_fmt yuv420p \
      "$s2"
  fi

  idx=$((idx+1))
done

# Concat list
list="$tmpdir/concat.txt"
: > "$list"
for f in "${stg2[@]}"; do
  # absolute paths to be safe
  ap="$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$f")"
  printf "file '%s'\n" "$ap" >> "$list"
done

# Concat (re-encode to ensure compatibility)
combined="$tmpdir/combined.avi"
echo "==> Concat ${#stg2[@]} parts"
if [[ -n "$FPS" ]]; then
  ffmpeg -hide_banner -loglevel "$loglvl" -y \
    -f concat -safe 0 -i "$list" \
    -an -r "$FPS" \
    -c:v mpeg4 -qscale:v "$QSCALE" -pix_fmt yuv420p \
    "$combined"
else
  ffmpeg -hide_banner -loglevel "$loglvl" -y \
    -f concat -safe 0 -i "$list" \
    -an \
    -c:v mpeg4 -qscale:v "$QSCALE" -pix_fmt yuv420p \
    "$combined"
fi

# Final
ext="${OUTPUT##*.}"; shopt -s nocasematch
case "$ext" in
  avi)
    echo "==> Finalizing AVI (apply GOP on final re-encode)"
    if [[ -n "$FPS" ]]; then
      ffmpeg -hide_banner -loglevel "$loglvl" -y \
        -i "$combined" -an -r "$FPS" \
        -c:v mpeg4 -qscale:v "$QSCALE" -g "$GOP" -bf 0 -sc_threshold 0 -pix_fmt yuv420p \
        "$OUTPUT"
    else
      ffmpeg -hide_banner -loglevel "$loglvl" -y \
        -i "$combined" -an \
        -c:v mpeg4 -qscale:v "$QSCALE" -g "$GOP" -bf 0 -sc_threshold 0 -pix_fmt yuv420p \
        "$OUTPUT"
    fi
    ;;
  mp4|mov|m4v)
    echo "==> Finalizing $ext with $CODEC (apply GOP)"
    if [[ -n "$FPS" ]]; then
      ffmpeg -hide_banner -loglevel "$loglvl" -y \
        -i "$combined" -an -r "$FPS" \
        -c:v "$CODEC" -g "$GOP" -bf 0 -sc_threshold 0 -pix_fmt yuv420p \
        -movflags +faststart \
        "$OUTPUT"
    else
      ffmpeg -hide_banner -loglevel "$loglvl" -y \
        -i "$combined" -an \
        -c:v "$CODEC" -g "$GOP" -bf 0 -sc_threshold 0 -pix_fmt yuv420p \
        -movflags +faststart \
        "$OUTPUT"
    fi
    ;;
  *)
    echo "Unknown output extension: .$ext"; exit 2 ;;
esac
shopt -u nocasematch

echo "✅ Done: $OUTPUT"
