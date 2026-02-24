#!/bin/bash
# Auto-update script: sync code with the subnet's latest release.
#
# Usage (from repo root):
#   chmod +x scripts/update_to_latest.sh       # once, make executable
#   ./scripts/update_to_latest.sh             # update to latest release tag
#   ./scripts/update_to_latest.sh main         # update to branch main instead
#   ./scripts/update_to_latest.sh --no-restart # skip pm2 restart
#
# Automatic daily update (cron, from repo root):
#   0 4 * * * cd /path/to/forever-money && ./scripts/update_to_latest.sh >> /var/log/forever-money-update.log 2>&1
#
# Requires: git, pip. Optional: pm2 (for validator/miner restart).

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Config
USE_BRANCH="${1:-}"
SKIP_RESTART=false
for arg in "$@"; do
  [ "$arg" = "--no-restart" ] && SKIP_RESTART=true
  [ "$arg" = "main" ] || [ "$arg" = "master" ] && USE_BRANCH="$arg"
done

echo "=============================================="
echo "SN98 ForeverMoney – update to latest release"
echo "=============================================="

# 1) Fetch latest from origin
echo "[1/6] Fetching from origin..."
git fetch origin --tags -q
echo "   Done."
echo ""

# 2) Check if update is actually needed
echo "[2/6] Checking for new changes..."
if [ -n "$USE_BRANCH" ]; then
    LOCAL_HASH=$(git rev-parse HEAD)
    REMOTE_HASH=$(git rev-parse "origin/$USE_BRANCH")
    TARGET_NAME="branch $USE_BRANCH"
else
    LATEST_TAG=$(git tag -l --sort=-v:refname | head -1)
    if [ -z "$LATEST_TAG" ]; then
        # Fallback to main/master if no tags
        DEFAULT_BRANCH=$(git remote show origin | sed -n '/HEAD branch/s/.*: //p')
        LOCAL_HASH=$(git rev-parse HEAD)
        REMOTE_HASH=$(git rev-parse "origin/$DEFAULT_BRANCH")
        TARGET_NAME="branch $DEFAULT_BRANCH"
    else
        LOCAL_HASH=$(git rev-parse HEAD)
        REMOTE_HASH=$(git rev-parse "$LATEST_TAG")
        TARGET_NAME="tag $LATEST_TAG"
    fi
fi

if [ "$LOCAL_HASH" = "$REMOTE_HASH" ]; then
    echo "   Already up to date with $TARGET_NAME. Skipping update."
    echo "=============================================="
    exit 0
fi
echo "   New changes detected ($TARGET_NAME). Proceeding..."
echo ""

# 3) Stash local changes
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "[3/6] Stashing local changes..."
  git stash push -m "update_to_latest_$(date +%Y%m%d_%H%M%S)" -- .
  STASHED=1
else
  echo "[3/6] Working tree clean."
  STASHED=0
fi

# 4) Checkout latest release or branch
echo "[4/6] Updating to latest..."
if [ -n "$USE_BRANCH" ]; then
  git checkout "$USE_BRANCH" -q
  git pull origin "$USE_BRANCH" -q
else
  if [ -n "$LATEST_TAG" ]; then
    git checkout "$LATEST_TAG" -q
  else
    git checkout "$DEFAULT_BRANCH" -q
    git pull origin "$DEFAULT_BRANCH" -q
  fi
fi
echo "   Done."

# 5) Restore stash
if [ "$STASHED" -eq 1 ]; then
  echo "[5/6] Restoring stashed changes..."
  git stash pop -q || true
else
  echo "[5/6] Nothing to restore."
fi

# 6) Reinstall dependencies and restart
echo "[6/6] Reinstalling dependencies..."
if [ -d ".venv" ]; then
  source .venv/bin/activate
  pip install -r requirements.txt -q
else
  pip install -r requirements.txt -q
fi

if [ "$SKIP_RESTART" = false ] && command -v pm2 &>/dev/null; then
  echo "Restarting pm2 processes..."
  pm2 restart all 2>/dev/null || true
  echo "   Done."
fi

echo "=============================================="
echo "Update complete."
echo "=============================================="