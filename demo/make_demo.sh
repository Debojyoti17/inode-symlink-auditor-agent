#!/usr/bin/env bash
# Builds /tmp/agent10_demo with broken links, loops, hard links and a suspicious link.
# No sudo needed. Safe to re-run (it rebuilds the folder).
set -eu
DEMO=/tmp/agent10_demo
HERE="$(cd "$(dirname "$0")" && pwd)"
"$HERE/cleanup.sh" >/dev/null

mkdir -p "$DEMO"/{desktop,documents,projects/backup,photos,locked}
echo "Quarterly report" > "$DEMO/documents/report.txt"
echo "Meeting notes"    > "$DEMO/documents/notes.txt"
echo "a,b,c"            > "$DEMO/projects/data.csv"

cd "$DEMO/desktop"
ln -s ../documents/report.txt   report-shortcut        # healthy, relative
ln -s ../documents              documents-folder       # healthy, link to a directory
ln -s ../projects/old-project   old-project            # BROKEN, relative
ln -s "$DEMO/photos/gone.jpg"   missing-photo          # BROKEN, absolute
ln -s self-link                 self-link              # LOOP (points at itself)
ln -s loop-b                    loop-a                 # LOOP (A -> B -> A)
ln -s loop-a                    loop-b
ln -s /etc/passwd               passwd-shortcut        # SUSPICIOUS (sensitive target)

ln "$DEMO/projects/data.csv" "$DEMO/projects/data-copy.csv"          # hard links
ln "$DEMO/projects/data.csv" "$DEMO/projects/backup/data-backup.csv"

echo "secret" > "$DEMO/locked/hidden.txt"
chmod 000 "$DEMO/locked"          # unreadable folder (skipped & reported when not root)

echo "Demo built in $DEMO:"
ls -lR "$DEMO" 2>/dev/null | grep -v '^total' || true
echo
echo "Inode numbers of the hard-linked files:"
ls -li "$DEMO/projects/data.csv" "$DEMO/projects/data-copy.csv" "$DEMO/projects/backup/data-backup.csv"
echo
echo "Expected: 2 broken links, 3 loop links, 1 suspicious link, 1 hard-link group (3 names)."
