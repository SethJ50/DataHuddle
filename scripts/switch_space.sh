#!/usr/bin/env bash
#
# Swaps which app is live on the Hugging Face Space, once per season.
#
#   ./scripts/switch_space.sh nfl     # DataHuddle goes live
#   ./scripts/switch_space.sh pga     # DataCaddie goes live
#   ./scripts/switch_space.sh status  # show what is live, change nothing
#
# ---------------------------------------------------------------------------
# How the swap works
# ---------------------------------------------------------------------------
# One Space, `SethJ50/data-caddie-shiny`, holds three branches:
#
#   main  -- WHATEVER IS LIVE. Hugging Face builds this branch and nothing
#            else. Rewritten on every switch, so never edit it directly.
#   nfl   -- DataHuddle's code. A copy of this repository's `main`.
#   pga   -- DataCaddie's code. The R/Shiny golf app.
#
# Switching means force-pushing `nfl` or `pga` onto `main`. Hugging Face sees a
# new commit, rebuilds the Docker image, and the other app is live in a few
# minutes.
#
# It has to work this way because there is no setting for it. Hugging Face
# always builds the file named `Dockerfile` at the root of `main` -- there is no
# `dockerfile_path` field in the Spaces config -- so the only way to change
# which app runs is to change what is at that path.
#
# ---------------------------------------------------------------------------
# Why the force-push is safe here
# ---------------------------------------------------------------------------
# Force-pushing normally means destroying history, which is why it has a bad
# reputation. It is fine in this one place because `main` holds nothing
# original: it is only ever a copy of `nfl` or `pga`, both of which this script
# refuses to touch. The real code always survives on its own branch.
#
# The `pga` branch matters more than it looks. The golf app's only git remote
# is the Space itself -- there is no GitHub backup -- so that branch IS the
# golf app. Step 1 of docs/HUGGINGFACE.md creates it, and this script checks it
# exists before doing anything that would overwrite `main`.

# Stop on the first error, on an undefined variable, and on a failure anywhere
# in a pipeline rather than only at its end. Without these, a failed `git fetch`
# would sail on into the force-push.
set -euo pipefail

SPACE_REMOTE="space"
SPACE_URL="https://huggingface.co/spaces/SethJ50/data-caddie-shiny"


# Print how to use this and stop. Called for a bad argument or no argument.
usage() {
    echo "usage: $0 {nfl|pga|status}"
    echo
    echo "  nfl     make DataHuddle live on the Space"
    echo "  pga     make DataCaddie live on the Space"
    echo "  status  show which app is live now"
    exit 1
}


# Check the Space is set up as a git remote here, and add it if it is not.
#
# `git remote get-url` exits non-zero when the remote does not exist. The
# `if !` around it turns that into a plain question rather than an error, which
# `set -e` above would otherwise treat as fatal.
ensure_remote() {
    if ! git remote get-url "$SPACE_REMOTE" > /dev/null 2>&1; then
        echo "Adding the '$SPACE_REMOTE' remote -> $SPACE_URL"
        git remote add "$SPACE_REMOTE" "$SPACE_URL"
    fi
}


# Report which app is currently live, by comparing commits.
#
# `git rev-parse` turns a branch name into the commit ID it points at. If the
# Space's `main` points at the same commit as `nfl`, then football is live.
show_status() {
    git fetch --quiet "$SPACE_REMOTE" 2>/dev/null || {
        echo "Could not reach the Space. Check your network and HF login."
        exit 1
    }

    local live nfl_head pga_head
    live=$(git rev-parse "$SPACE_REMOTE/main")
    nfl_head=$(git rev-parse "$SPACE_REMOTE/nfl" 2>/dev/null || echo "none")
    pga_head=$(git rev-parse "$SPACE_REMOTE/pga" 2>/dev/null || echo "none")

    echo "Space: $SPACE_URL"
    if [ "$live" = "$nfl_head" ]; then
        echo "Live:  nfl (DataHuddle)"
    elif [ "$live" = "$pga_head" ]; then
        echo "Live:  pga (DataCaddie)"
    else
        # Happens if somebody committed straight to `main` on the HF website,
        # which the web editor makes easy to do by accident.
        echo "Live:  UNKNOWN -- main ($live) matches neither branch."
        echo "       Something was committed to main directly. Whatever it is"
        echo "       will be lost by the next switch, so save it first."
    fi
    echo "nfl:   $nfl_head"
    echo "pga:   $pga_head"
}


# Publish one branch to the Space, making that app live.
#
# Args:
#   $1 -- the branch to promote, "nfl" or "pga".
promote() {
    local branch="$1"

    git fetch --quiet "$SPACE_REMOTE"

    # Refuse if the branch is missing. Without this check, promoting a
    # mistyped branch name would fail confusingly halfway through -- and if the
    # missing branch were `pga`, its absence is exactly the thing that would
    # mean the golf app was never backed up.
    if ! git rev-parse --verify --quiet "$SPACE_REMOTE/$branch" > /dev/null; then
        echo "There is no '$branch' branch on the Space."
        [ "$branch" = "pga" ] && echo "Run step 1 of docs/HUGGINGFACE.md first -- the golf app is not backed up."
        exit 1
    fi

    echo "Promoting '$branch' to live..."

    # Pushes the fetched copy of that branch straight onto main. Reading from
    # refs/remotes means this works for `pga` too, whose code is not in this
    # repository at all -- git will happily push any commit it has, whoever
    # wrote it.
    git push --force "$SPACE_REMOTE" \
        "refs/remotes/$SPACE_REMOTE/$branch:refs/heads/main"

    echo
    echo "Done. Hugging Face is rebuilding now -- give it a few minutes."
    echo "Watch it: $SPACE_URL  (the Logs tab, 'Build' view)"
}


# Copy this repository's current `main` up to the Space's `nfl` branch.
#
# Run before promoting nfl, so that what goes live is what is committed here
# rather than whatever was pushed months ago.
sync_nfl() {
    local current
    current=$(git rev-parse --abbrev-ref HEAD)

    if [ "$current" != "main" ]; then
        echo "You are on branch '$current', not main. Switch to main first."
        exit 1
    fi

    # A deploy of code that only exists on your laptop is a deploy nobody can
    # reproduce, so insist everything is committed. `--porcelain` prints one
    # line per changed file and nothing at all when the tree is clean.
    if [ -n "$(git status --porcelain)" ]; then
        echo "You have uncommitted changes. Commit or stash them first:"
        git status --short
        exit 1
    fi

    echo "Pushing local main -> $SPACE_REMOTE/nfl"
    git push --force "$SPACE_REMOTE" main:nfl
}


case "${1:-}" in
    nfl)
        ensure_remote
        sync_nfl
        promote nfl
        ;;
    pga)
        ensure_remote
        # No sync step: the golf app's code is not in this repository. Its
        # `pga` branch is updated from ~/Desktop/DataCaddie/data-caddie-shiny,
        # by pushing there -- see docs/HUGGINGFACE.md.
        promote pga
        ;;
    status)
        ensure_remote
        show_status
        ;;
    *)
        usage
        ;;
esac
