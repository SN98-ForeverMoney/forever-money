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

# Config (override with env if needed)
USE_BRANCH="${1:-}"
SKIP_RESTART=false
for arg in "$@"; do
  [ "$arg" = "--no-restart" ] && SKIP_RESTART=true
  [ "$arg" = "main" ] || [ "$arg" = "master" ] && USE_BRANCH="$arg"
done

echo "=============================================="
echo "SN98 ForeverMoney – update to latest release"
echo "=============================================="
echo "Repo root: $REPO_ROOT"
echo ""

# 1) Fetch latest from origin
echo "[1/5] Fetching from origin..."
git fetch origin --tags
echo "   Done."
echo ""

# 2) Stash local changes if any (we won't touch .env; it's usually gitignored)
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "[2/5] Stashing local changes..."
  git stash push -m "update_to_latest_$(date +%Y%m%d_%H%M%S)" -- .
  STASHED=1
else
  echo "[2/5] Working tree clean."
  STASHED=0
fi
echo ""

# 3) Checkout latest release or branch
echo "[3/5] Updating to latest..."
if [ -n "$USE_BRANCH" ]; then
  echo "   Using branch: $USE_BRANCH"
  git checkout "$USE_BRANCH"
  git pull origin "$USE_BRANCH"
else
  # Latest semantic-version tag (e.g. v1.2.3) from origin
  LATEST_TAG="$(git tag -l --sort=-v:refname 2>/dev/null | head -1)"
  if [ -n "$LATEST_TAG" ]; then
    echo "   Using latest release tag: $LATEST_TAG"
    git checkout "$LATEST_TAG"
  else
    echo "   No tags found; using origin/main."
    git checkout main 2>/dev/null || git checkout master 2>/dev/null || true
    git pull origin main 2>/dev/null || git pull origin master 2>/dev/null || true
  fi
fi
echo "   Done."
echo ""

# 4) Restore stash if we stashed
if [ "$STASHED" -eq 1 ]; then
  echo "[4/5] Restoring stashed changes..."
  git stash pop || true
  echo "   Done (resolve conflicts if any)."
else
  echo "[4/5] Nothing to restore."
fi
echo ""

# 5) Reinstall dependencies and optional restart
echo "[5/5] Reinstalling dependencies..."
if [ -d ".venv" ]; then
  source .venv/bin/activate
  pip install -r requirements.txt -q
  echo "   Done (using .venv)."
else
  pip install -r requirements.txt -q
  echo "   Done."
fi

if [ "$SKIP_RESTART" = false ] && command -v pm2 &>/dev/null; then
  echo ""
  echo "Restarting pm2 processes..."
  pm2 restart all 2>/dev/null || true
  echo "   Done."
fi

echo ""
echo "=============================================="
echo "Update complete."
echo "=============================================="
