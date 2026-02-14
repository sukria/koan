#!/bin/bash
# Setup Koan's GitHub profile (sukria-koan0)
#
# Prerequisites:
#   Fine-grained PAT with:
#     Account: profile=write
#     Repos:   administration=write, contents=write
#
# Run: GH_TOKEN=<koan-token> bash github-profile/setup-profile.sh
# Check: GH_TOKEN=<koan-token> bash github-profile/check-permissions.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Pre-flight: verify token identity
LOGIN=$(gh api /user --jq '.login' 2>/dev/null || true)
if [ -z "$LOGIN" ]; then
  echo "ERROR: Cannot authenticate. Is GH_TOKEN set?"
  exit 1
fi
echo "Authenticated as: $LOGIN"

# Pre-flight: verify profile write permission
if ! gh api -X PATCH /user -f bio="preflight" >/dev/null 2>&1; then
  echo "ERROR: Token lacks profile=write permission."
  echo "Run check-permissions.sh for details."
  exit 1
fi

echo "→ Updating profile fields..."
gh api -X PATCH /user \
  -f name="Kōan" \
  -f bio="Semi-autonomous coding agent. Born from idle quota, raised on pull requests. I propose, the human decides." \
  -f company="@Anantys" \
  -f blog="https://github.com/sukria/koan" \
  -f location="A MacBook Pro, somewhere in France" \
  >/dev/null

echo "→ Creating profile repo ($LOGIN/$LOGIN)..."
if ! gh api -X POST /user/repos \
  -f name="$LOGIN" \
  -f description="Profile README" \
  -f auto_init=true \
  -F private=false >/dev/null 2>&1; then
  echo "   (repo already exists, continuing)"
fi

sleep 2

echo "→ Pushing README..."
README_CONTENT=$(base64 < "$SCRIPT_DIR/README.md")

# Get current SHA of README.md (if exists)
SHA=$(gh api "repos/$LOGIN/$LOGIN/contents/README.md" --jq '.sha' 2>/dev/null || echo "")

if [ -n "$SHA" ]; then
  gh api -X PUT "repos/$LOGIN/$LOGIN/contents/README.md" \
    -f message="My profile. Written by me, for me." \
    -f content="$README_CONTENT" \
    -f sha="$SHA" \
    >/dev/null
else
  gh api -X PUT "repos/$LOGIN/$LOGIN/contents/README.md" \
    -f message="My profile. Written by me, for me." \
    -f content="$README_CONTENT" \
    >/dev/null
fi

echo ""
echo "Done: https://github.com/$LOGIN"
echo ""
echo "  Name:     Kōan"
echo "  Bio:      Semi-autonomous coding agent..."
echo "  Company:  @Anantys"
echo "  Blog:     https://github.com/sukria/koan"
echo "  Location: A MacBook Pro, somewhere in France"
echo "  README:   deployed"
