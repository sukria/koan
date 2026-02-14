#!/bin/bash
# Check that the GH_TOKEN has all required permissions for sukria-koan0
# Run: GH_TOKEN=<koan-token> bash github-profile/check-permissions.sh

set -uo pipefail

echo "=== GitHub Token Permissions Audit ==="
echo ""

# 1. Who am I?
LOGIN=$(gh api /user --jq '.login' 2>/dev/null) || true
if [ -z "$LOGIN" ]; then
  echo "FAIL: Token cannot authenticate at all"
  exit 1
fi
echo "Authenticated as: $LOGIN"
echo ""

PASS=0
FAIL=0

ok()   { echo "  OK  $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL $1 — needs: $2"; FAIL=$((FAIL + 1)); }

# 2. Profile write (PATCH /user)
echo "--- Account permissions ---"
if gh api -X PATCH /user -f bio="test" >/dev/null 2>&1; then
  gh api -X PATCH /user \
    -f bio="Semi-autonomous coding agent. Born from idle quota, raised on pull requests. I propose, the human decides." \
    >/dev/null 2>&1
  ok "Profile write (PATCH /user)"
else
  fail "Profile write (PATCH /user)" "profile=write"
fi

# 3. Repo creation (POST /user/repos)
if gh api -X POST /user/repos -f name="__perm-test__" -F private=true >/dev/null 2>&1; then
  gh api -X DELETE "/repos/$LOGIN/__perm-test__" >/dev/null 2>&1
  ok "Repo creation (POST /user/repos)"
else
  fail "Repo creation (POST /user/repos)" "administration=write"
fi

# 4. Repository-specific permissions on sukria/koan
echo ""
echo "--- Repository permissions (sukria/koan) ---"

# Contents (push)
PUSH=$(gh api /repos/sukria/koan --jq '.permissions.push' 2>/dev/null) || true
if [ "$PUSH" = "true" ]; then
  ok "Contents write (push)"
else
  fail "Contents write (push)" "contents=write on sukria/koan"
fi

# Issues
if gh api -X POST /repos/sukria/koan/issues -f title="__perm-test__" -f body="auto-cleanup" >/dev/null 2>&1; then
  ok "Issues write"
else
  fail "Issues write" "issues=write on sukria/koan"
fi

# Pull requests (read)
if gh api /repos/sukria/koan/pulls --jq 'length' >/dev/null 2>&1; then
  ok "Pull requests read"
else
  fail "Pull requests read" "pull_requests=read on sukria/koan"
fi

# Pull requests (write) — check via accepted permissions header
PR_HEADER=$(gh api -i -X POST /repos/sukria/koan/pulls -f title="x" -f head="main" -f base="main" 2>&1) || true
if echo "$PR_HEADER" | grep -q "pull_requests=write"; then
  fail "Pull requests write" "pull_requests=write on sukria/koan"
else
  ok "Pull requests write"
fi

echo ""
echo "--- Summary ---"
echo "  Passed: $PASS"
echo "  Failed: $FAIL"
echo ""

if [ $FAIL -gt 0 ]; then
  echo "Token needs additional permissions."
  echo ""
  echo "To fix: go to https://github.com/settings/personal-access-tokens"
  echo "Edit the fine-grained PAT for sukria-koan0 and ensure:"
  echo ""
  echo "  Account permissions:"
  echo "    - Profile: Read and write"
  echo ""
  echo "  Repository access:"
  echo "    - Add 'sukria/koan' (and all Koan-managed repos)"
  echo ""
  echo "  Repository permissions:"
  echo "    - Administration: Read and write (for repo creation)"
  echo "    - Contents: Read and write (for git push)"
  echo "    - Issues: Read and write"
  echo "    - Pull requests: Read and write"
  echo "    - Metadata: Read (auto-granted)"
  exit 1
else
  echo "All permissions OK. Ready to run setup-profile.sh"
fi
