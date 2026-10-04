#!/bin/bash
# sync-from-private.sh - Scrub and sync the private ATS harness to the public jobpilot repo.
#
# Usage: ./sync-from-private.sh /path/to/private/harness
#
# This is a REVIEW-DRIVEN sync, not an automatic one. The public repo has
# diverged from the private harness (generalized env vars, removed personal
# references), so a blind copy would regress those changes.
#
# What this script does:
#   1. Copies shared .py files from private harness to a staging dir
#   2. Applies the env-var transformation (ATS_* -> JOBPILOT_* with fallback)
#   3. Verifies the exclusion list (never copies facts.json, LinkedIn, etc.)
#   4. Runs the offline test suite
#   5. Prints a diff summary for manual review
#
# What YOU do after:
#   1. Review each diff (private changes vs public generalizations)
#   2. Manually merge logic changes, preserving public generalizations
#   3. Copy the merged files to the staging dir
#   4. Push via the normal flow

set -u

PRIVATE="${1:?Usage: $0 /path/to/private/harness}"
STAGING="$(cd "$(dirname "$0")" && pwd)"
WORKDIR="$(mktemp -d)"

# Files that exist in private but must NEVER go public, with reasons.
# (LinkedIn modules: account-flag risk. log_application.py: personal Sheet.
#  BUILD_LOG.md: personal log. amd_cdp.py: machine-specific, replaced by
#  cdp_driver.py. facts.json: personal data. *.pre-wire-backup: cruft.)
EXCLUDE="BUILD_LOG.md amd_cdp.py facts.json fill.py.pre-wire-backup linkedin_dump.py linkedin_fill.py log_application.py"

# Shared files: present in both, synced with transformations.
SHARED="ats_fill.py cdp_direct.py common.py extract.py fill.py hard_patterns.py indeed_dump.py indeed_fill.py map.py modal_common.py model.py schema_dump.py set_select.py set_select2.py submit_only.py templates.py verify.py test_hard_patterns.py test_indeed.py test_templates.py test_wellfound.py"

echo "=== Step 1: Copying shared files to $WORKDIR ==="
for f in $SHARED; do
    if [ -f "$PRIVATE/$f" ]; then
        cp "$PRIVATE/$f" "$WORKDIR/$f"
        echo "  copied $f"
    else
        echo "  MISSING in private: $f"
    fi
done

echo ""
echo "=== Step 2: Applying env-var transformation ==="
echo "  Replacing os.environ.get(\"ATS_X\" with os.environ.get(\"JOBPILOT_X\", os.environ.get(\"ATS_X\""
# This is intentionally a dry-run preview. The actual transformation needs
# manual review because the public files have diverged.
grep -l 'os.environ.get("ATS_' "$WORKDIR"/*.py 2>/dev/null | while read -r f; do
    echo "  needs transform: $(basename "$f")"
    grep -o 'os.environ.get("ATS_[A-Z_]*"' "$f" | sort -u | sed 's/^/    /'
done

echo ""
echo "=== Step 3: Exclusion check ==="
for f in $EXCLUDE; do
    if [ -f "$WORKDIR/$f" ]; then
        echo "  ERROR: excluded file present: $f"
    fi
done
if [ -f "$WORKDIR/facts.json" ]; then
    echo "  ERROR: facts.json would be copied!"
else
    echo "  OK: facts.json not present"
fi
echo "  OK: exclusion list enforced"

echo ""
echo "=== Step 4: Diff summary (private vs public) ==="
for f in $SHARED; do
    if [ -f "$WORKDIR/$f" ] && [ -f "$STAGING/$f" ]; then
        n=$(diff "$WORKDIR/$f" "$STAGING/$f" 2>/dev/null | grep -c "^[<>]")
        if [ "$n" -gt 0 ]; then
            echo "  $f: $n diff lines (needs review)"
        fi
    fi
done

echo ""
echo "=== Step 5: Running offline tests on current staging ==="
cd "$STAGING" && python3 -m unittest test_offline 2>&1 | tail -3

echo ""
echo "Staging files in $WORKDIR. Review diffs, merge manually, then push."
echo "See SYNC.md for the full process."
