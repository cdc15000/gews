#!/bin/bash
# Upload all large GEWS data to Google Drive and delete local copies
set -e

REMOTE="gdrive:gews-data"
DATA="/Users/laptop-h9y2/projects/claude/gews/notebooks/data"

upload_and_clean() {
    local src="$1"
    local dst="$2"
    local label="$3"

    if [ ! -d "$src" ] || [ -z "$(ls -A "$src" 2>/dev/null)" ]; then
        echo "SKIP $label (empty or missing)"
        return
    fi

    local local_count=$(find "$src" -type f | wc -l | tr -d ' ')
    local local_size=$(du -sh "$src" | cut -f1)
    echo "UPLOADING $label: $local_count files, $local_size"

    rclone copy "$src" "$REMOTE/$dst" --transfers=4 --retries=3 --low-level-retries=10 2>&1 | grep -v "^$" || true

    # Verify remote count matches
    local remote_count=$(rclone ls "$REMOTE/$dst" 2>/dev/null | wc -l | tr -d ' ')
    echo "VERIFY $label: local=$local_count remote=$remote_count"

    if [ "$remote_count" -ge "$local_count" ]; then
        echo "DELETING local $label ($local_size)"
        rm -rf "$src"
        mkdir -p "$src"  # recreate empty dir
        echo "DONE $label — freed $local_size"
    else
        echo "WARN $label: remote count ($remote_count) < local ($local_count), keeping local"
    fi
}

# Wait for any existing GUNW upload to finish
if pgrep -f "rclone copy.*gunw" >/dev/null 2>&1; then
    echo "WAITING for existing GUNW upload to finish..."
    while pgrep -f "rclone copy.*gunw" >/dev/null 2>&1; do
        sleep 30
        remote_size=$(rclone size "$REMOTE/nisar/gunw" 2>/dev/null | grep "Total size" | sed 's/.*: //' || echo "unknown")
        echo "PROGRESS GUNW: $remote_size uploaded"
    done
    echo "Existing GUNW upload finished"
fi

upload_and_clean "$DATA/nisar/gunw" "nisar/gunw" "GUNW"
upload_and_clean "$DATA/nisar/goff" "nisar/goff" "GOFF"
upload_and_clean "$DATA/hyp3_products" "hyp3_products" "HyP3"
upload_and_clean "$DATA/mintpy" "mintpy" "MintPy"

# Also upload burst granules JSON and any other small files worth keeping
for f in "$DATA"/*.json "$DATA"/*.py; do
    [ -f "$f" ] && rclone copy "$f" "$REMOTE/" 2>/dev/null
done

echo "---"
echo "FINAL remote size:"
rclone size "$REMOTE" 2>/dev/null
echo "---"
df -h /Users/laptop-h9y2 | tail -1
echo "ALL_UPLOADS_COMPLETE"
