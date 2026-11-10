#!/bin/sh
# Fetch the third-party sources at the pinned commits into DIR (default: work). Nothing here is redistributed.
set -eu
dir=${1:-work}
here=$(dirname "$0")
mkdir -p "$dir"
grep -v '^#' "$here/sources.tsv" | while IFS="$(printf '\t')" read -r name url commit _; do
    [ -d "$dir/$name/.git" ] && continue
    git init -q "$dir/$name"
    git -C "$dir/$name" fetch -q --depth 1 "$url" "$commit"
    git -C "$dir/$name" -c advice.detachedHead=false checkout -q FETCH_HEAD
    echo "$name $(git -C "$dir/$name" rev-parse HEAD)"
done
