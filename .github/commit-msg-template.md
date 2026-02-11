# Format: <type>(<scope>): <subject>
#
# Types: feat | fix | docs | refactor | test | chore | perf | ci | build | revert
# Scope: bridge, runner, missions, skills, config, github, provider, tests, ...
# Subject: imperative, lowercase, ≤72 chars, no period
#
# See docs/commit-conventions.md for full specification


Case <PROJ-123 or #123 or N/A>:

# Describe what changed and why. Keep lines under 100 characters.


Changelog:

# ──────────────────────────────────────────────────────────────────
# REMINDERS:
# - Case ID is required: use JIRA (PROJ-123), GitHub (#123), or N/A
# - Changelog is MANDATORY — leave empty if no customer-facing change
# - Changelog must be the LAST line (after Co-Authored-By and Refs)
# - Footer order: Co-Authored-By → Refs → Changelog
#
# To enable this template:
#   git config commit.template .github/commit-msg-template.md
# ──────────────────────────────────────────────────────────────────
