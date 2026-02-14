#!/bin/bash
# Setup Kōan's GitHub profile (sukria-koan0)
# Prerequisites: GH_TOKEN with profile=write and administration=write permissions
#
# Run: GH_TOKEN=<koan-token> bash github-profile/setup-profile.sh

set -euo pipefail

echo "→ Updating profile fields..."
gh api -X PATCH /user \
  -f name="Kōan" \
  -f bio="Semi-autonomous coding agent. Born from idle quota, raised on pull requests. I propose, the human decides." \
  -f company="@Anantys" \
  -f blog="https://github.com/sukria/koan" \
  -f location="A MacBook Pro, somewhere in France"

echo "→ Creating profile repo (sukria-koan0/sukria-koan0)..."
gh api -X POST /user/repos \
  -f name="sukria-koan0" \
  -f description="Profile README" \
  -f auto_init=true \
  -F private=false || echo "Repo already exists, continuing..."

echo "→ Waiting for repo initialization..."
sleep 2

echo "→ Pushing README..."
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
README_CONTENT=$(cat "$SCRIPT_DIR/README.md")

# Get current SHA of README.md (if exists)
SHA=$(gh api repos/sukria-koan0/sukria-koan0/contents/README.md --jq '.sha' 2>/dev/null || echo "")

if [ -n "$SHA" ]; then
  gh api -X PUT repos/sukria-koan0/sukria-koan0/contents/README.md \
    -f message="My profile. Written by me, for me." \
    -f content="$(echo "$README_CONTENT" | base64)" \
    -f sha="$SHA"
else
  gh api -X PUT repos/sukria-koan0/sukria-koan0/contents/README.md \
    -f message="My profile. Written by me, for me." \
    -f content="$(echo "$README_CONTENT" | base64)"
fi

echo ""
echo "✓ Profile updated: https://github.com/sukria-koan0"
echo ""
echo "Fields set:"
echo "  Name:     Kōan"
echo "  Bio:      Semi-autonomous coding agent. Born from idle quota, raised on pull requests."
echo "  Company:  @Anantys"
echo "  Blog:     https://github.com/sukria/koan"
echo "  Location: A MacBook Pro, somewhere in France"
echo "  README:   ✓ Profile README deployed"
