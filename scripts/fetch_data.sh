#!/usr/bin/env bash
# Fetch the pinned Jira dataset into the data folder, check its md5, and
# write `dataset.receipt` next to it for `orgos mongo restore` (spec 0003).
#
#   scripts/fetch_data.sh [data dir]     # default: <repo root>/data
#
# Reuses a zip already in place, resumes an interrupted download from its
# `.part`, and refuses to go on when an md5 differs. Needs only bash, curl,
# md5sum (or md5), wc, date, mkdir and mv: no .env, no Docker, no Python.
# Run from the repo root (or set ORGOS_DATA_DIR to the same folder) so
# `orgos` finds the receipt.

set -euo pipefail

# The pinned facts: the only place in code that names them. The README's
# data manifest documents them.
RECORD=15719919
VERSION=2025-06-23
URL='https://zenodo.org/records/15719919/files/2025-06-23%20ThePublicJiraDataset.zip?download=1'
NAME='2025-06-23 ThePublicJiraDataset.zip'
MD5=02f85309d966092ea130ca0797aea795
SIZE=5813135238

RECEIPT=dataset.receipt

fail() {
    printf 'fetch_data: %s\n' "$*" >&2
    exit 1
}

have() {
    command -v "$1" >/dev/null 2>&1
}

md5_of() {
    local out
    if have md5sum; then
        out=$(md5sum <"$1")
    else
        out=$(md5 -q <"$1")
    fi
    printf '%s' "${out%% *}"
}

size_of() {
    local bytes
    bytes=$(wc -c <"$1")
    printf '%s' "$((bytes))"
}

write_receipt() {
    local dir=$1 name=$2 url=$3 md5=$4 size=$5
    {
        printf 'file=%s\n' "$name"
        printf 'size=%s\n' "$size"
        printf 'md5=%s\n' "$md5"
        printf 'record=%s\n' "$RECORD"
        printf 'version=%s\n' "$VERSION"
        printf 'url=%s\n' "$url"
        printf 'verified_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    } >"$dir/$RECEIPT.part"
    mv -f "$dir/$RECEIPT.part" "$dir/$RECEIPT"
}

# fetch_dataset DIR URL NAME MD5 SIZE: make DIR/NAME exist with md5 MD5,
# downloading URL to DIR/NAME.part first, then write DIR/dataset.receipt.
fetch_dataset() {
    local dir=$1 url=$2 name=$3 md5=$4 size=$5
    local final="$dir/$name" part="$dir/$name.part" actual got

    have md5sum || have md5 || fail "needs md5sum or md5 on PATH"
    mkdir -p "$dir"

    if [[ -e $final ]]; then
        actual=$(md5_of "$final")
        if [[ $actual != "$md5" ]]; then
            fail "$final has md5 $actual, but the pinned md5 is $md5." \
                "Left it untouched and wrote no receipt; move or delete it, then run again."
        fi
        write_receipt "$dir" "$name" "$url" "$md5" "$size"
        printf 'fetch_data: ready, %s already in place (md5 ok), receipt written\n' "$final" >&2
        return 0
    fi

    got=0
    if [[ -e $part ]]; then
        got=$(size_of "$part")
    fi
    if ((got < size)); then
        have curl || fail "needs curl on PATH"
        if ((got > 0)); then
            printf 'fetch_data: resuming %s at %s of %s bytes\n' "$part" "$got" "$size" >&2
        fi
        curl -L --fail -C - -o "$part" "$url" ||
            fail "download stopped (curl exit $?); run again to resume from $part"
    fi

    actual=$(md5_of "$part")
    if [[ $actual != "$md5" ]]; then
        fail "$part has md5 $actual, but the pinned md5 is $md5." \
            "Delete $part, then run again. No receipt written."
    fi
    mv -f "$part" "$final"
    write_receipt "$dir" "$name" "$url" "$md5" "$size"
    printf 'fetch_data: ready, %s (md5 ok), receipt written\n' "$final" >&2
}

main() {
    local here=${BASH_SOURCE[0]} root
    if [[ $here == */* ]]; then
        here=${here%/*}
    else
        here=.
    fi
    root=$(cd "$here/.." && pwd)
    fetch_dataset "${1:-$root/data}" "$URL" "$NAME" "$MD5" "$SIZE"
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    main "$@"
fi
