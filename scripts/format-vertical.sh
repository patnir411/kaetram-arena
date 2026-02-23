#!/usr/bin/env bash
# Convert 16:9 clip to 9:16 vertical with caption overlay
# Usage: format-vertical.sh <input.mp4> [caption] [output.mp4]
INPUT="$1"
CAPTION="${2:-AI plays Kaetram}"
OUTPUT="${3:-${INPUT%.*}_vertical.mp4}"

ESCAPED=$(printf '%s' "$CAPTION" | sed "s/:/\\\\:/g; s/'/\\\\'/g")

ffmpeg -y -i "$INPUT" \
  -vf "crop=ih*(9/16):ih,scale=1080:1920:flags=lanczos,\
drawtext=text='${ESCAPED}':fontsize=48:fontcolor=white:\
borderw=3:bordercolor=black:x=(w-text_w)/2:y=h*0.75" \
  -c:v libx264 -preset medium -crf 23 \
  -c:a aac -b:a 128k -movflags +faststart "$OUTPUT"
echo "Vertical clip saved: $OUTPUT"
