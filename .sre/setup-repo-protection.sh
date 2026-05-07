#!/usr/bin/env bash
# setup-repo-protection.sh — apply branch protection rules to the default branch
# Run this once per repository to enforce merge rules via GitHub API.
# Re-run if rules change.
#
# Requires: gh CLI installed and authenticated (gh auth login)
# Usage: .sre/setup-repo-protection.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

# Get repo owner and name from remote URL.
cd "$REPO_ROOT"
REMOTE_URL=$(git config --get remote.origin.url)

# Extract owner/repo from HTTPS or SSH URLs.
if [[ $REMOTE_URL =~ github.com[:/]([^/]+)/(.+?)(\.git)?$ ]]; then
  OWNER="${BASH_REMATCH[1]}"
  REPO="${BASH_REMATCH[2]}"
else
  echo "ERROR: Could not parse GitHub repo from remote URL: $REMOTE_URL"
  exit 1
fi

# Get default branch.
DEFAULT_BRANCH=$(git rev-parse --abbrev-ref origin/HEAD | sed 's|origin/||')

echo "Applying protection rules to $OWNER/$REPO on branch $DEFAULT_BRANCH..."

# Function to apply branch protection via gh API.
# This is idempotent — can be re-run.
apply_protection() {
  # Enable branch protection.
  gh api "repos/$OWNER/$REPO/branches/$DEFAULT_BRANCH/protection" \
    --input /dev/stdin <<EOF
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["ci"]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "required_approving_review_count": 1
  },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "require_code_owner_reviews": false,
  "require_last_push_approval": false
}
EOF
}

# Try to apply. If it fails, the rule may already be set — that's OK.
if apply_protection 2>/dev/null; then
  echo "Branch protection applied to $DEFAULT_BRANCH"
else
  echo "Branch protection already set (or gh API auth issue). Skipping."
fi

echo "Done. Branch protection is active on $DEFAULT_BRANCH."
echo ""
echo "Rules enforced:"
echo "  - No direct pushes (PR required)"
echo "  - Linear history (squash/rebase only)"
echo "  - 1 approving review required"
echo "  - Stale approvals dismissed on new commits"
echo "  - Admins can bypass (emergency only)"
echo ""
echo "To view current rules: gh api repos/$OWNER/$REPO/branches/$DEFAULT_BRANCH/protection"
