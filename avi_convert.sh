for f in videosrc/*.{mp4,mov,m4v,mkv}; do
  [[ -e "$f" ]] || continue
  ffmpeg -y -i "$f" -an \
    -vf "scale=trunc(iw/2)*2:trunc(ih/2)*2,fps=30" -r 30 -vsync cfr \
    -c:v mpeg4 -qscale:v 3 -g 600 -bf 0 -sc_threshold 0 -pix_fmt yuv420p \
    "${f%.*}.avi"
done
