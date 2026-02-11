# Commit Message Conventions

This document defines the standardized commit message format for the Kōan project, based on the [Conventional Commits](https://www.conventionalcommits.org/) specification with integrated case tracking (JIRA and GitHub issues).

Every commit **must** follow this format. Both human contributors and AI agents (including Kōan) are expected to comply.

---

## Format

```
<type>(<scope>): <subject>

Case <ID>:

<description>

Co-Authored-By: <name> <<email>>
Refs: <references>
Changelog: <customer-facing change or empty>
```

### Structure

| Section | Required | Description |
|---------|----------|-------------|
| **type** | ✅ | Semantic category of the change |
| **scope** | Optional | Module, component, or functional area affected |
| **subject** | ✅ | Imperative, lowercase summary (≤72 chars) |
| **Case ID** | ✅ | JIRA ticket or GitHub issue reference (use `N/A` if none) |
| **description** | ✅ | What changed and why, in one or more paragraphs |
| **Co-Authored-By** | When applicable | Attribution for pair programming or AI assistance |
| **Refs** | Optional | Related issues, PRs, or documentation links |
| **Changelog** | ✅ | Customer-facing summary — **always present, even if empty** |

### Footer Ordering Rule

Footers must appear in this order:

1. `Co-Authored-By:` (one per author)
2. `Refs:` (related links)
3. **`Changelog:` — always last**

This ordering is mandatory. Changelog as the final line enables reliable parsing and automation.

---

## Commit Types

| Type | Description | Example |
|------|-------------|---------|
| `feat` | New feature or capability | `feat(bridge): add Slack messaging provider` |
| `fix` | Bug fix | `fix(missions): prevent duplicate mission entries` |
| `docs` | Documentation only | `docs: add LLM provider setup guides` |
| `refactor` | Code restructuring (no behavior change) | `refactor(run): replace subprocess calls with imports` |
| `test` | Adding or updating tests | `test(github): comprehensive notification tests` |
| `chore` | Maintenance, dependencies, tooling | `chore: update Python version in CI matrix` |
| `perf` | Performance improvement | `perf(missions): cache parsed mission list` |
| `ci` | CI/CD pipeline changes | `ci: add Python 3.14 to test matrix` |
| `build` | Build system or external dependencies | `build: add Flask to requirements.txt` |
| `revert` | Reverting a previous commit | `revert: feat(bridge): add Slack provider` |

### Choosing the Right Type

- Changed behavior → `feat` or `fix`
- Same behavior, cleaner code → `refactor`
- Only `.md` or comments → `docs`
- Only test files → `test`
- CI config or scripts → `ci`
- Dependency updates, configs → `chore` or `build`

---

## Scope

The scope is optional but recommended. It narrows the change to a specific area.

### Common scopes for Kōan

| Scope | Area |
|-------|------|
| `bridge` | Telegram bridge (`awake.py`, `command_handlers.py`) |
| `run` | Agent loop (`run.py`, `mission_runner.py`) |
| `missions` | Mission queue (`missions.py`) |
| `skills` | Skill system (`skills.py`, individual skills) |
| `config` | Configuration (`projects_config.py`, `config.yaml`) |
| `github` | GitHub integration (`github.py`, `github_notifications.py`) |
| `provider` | CLI provider abstraction (`provider/`) |
| `tests` | Test infrastructure |
| `plan` | Plan command / output |
| `status` | Status display / reporting |
| `loop` | Main loop logic (`loop_manager.py`, `iteration_manager.py`) |
| `schedule` | Scheduling, quota, timing |

For multi-scope changes, either pick the primary scope or use comma-separated values:
```
feat(bridge,runner): add restart signal between processes
```

---

## Subject Line

- Imperative mood: "add feature" not "added feature" or "adds feature"
- Lowercase first letter (no capitalization after the colon)
- No period at the end
- Maximum 72 characters total (type + scope + subject)
- Describe **what** the commit does, not **how**

Good:
```
feat(bridge): add restart support for run process
fix(missions): prevent duplicate entries on concurrent writes
```

Bad:
```
feat(bridge): Added restart support.    # past tense, period
Fix: Bug in missions                    # capitalized, vague
feat(bridge): implements the ability to restart the run process from the bridge via file-based signaling   # too long
```

---

## Case ID

Every commit body starts with a case ID line linking the change to a tracked issue.

### JIRA Format

```
Case KOAN-242:
Case PROJ-456:
Case ABC-789:
```

The project key is flexible alphanumeric (`[A-Z0-9]+`), followed by a dash and a number. No maintained list — new projects are created regularly.

### GitHub Issue Format

For open-source projects or when a GitHub issue is the primary tracker:

```
Case #242:
Case #15:
```

### Multiple Case IDs

```
Case KOAN-242, PROJ-456:
```

Primary case first.

### No Case ID

When no tracked issue exists (typo fixes, internal tooling, urgent hotfixes):

```
Case N/A:
```

This is explicit — it means "no issue exists for this change," not "I forgot to add one."

---

## Description Body

After the case ID, write one or more paragraphs explaining:

- **What** changed
- **Why** it was necessary
- **How** it works (if non-obvious)

Keep lines under 100 characters. Be factual and concise.

```
Case KOAN-242:

Add comprehensive commit message convention document following the
Conventional Commits specification. Integrates JIRA and GitHub issue
tracking with mandatory Changelog footer.

The format supports both commercial (JIRA) and open-source (GitHub)
workflows. Footer ordering is standardized: Co-Authors → Refs → Changelog
(always last) to enable reliable automated parsing.
```

---

## Co-Authored-By

Attribution for pair programming or AI-assisted commits. One line per author:

```
Co-Authored-By: Claude <noreply@anthropic.com>
Co-Authored-By: GitHub Copilot <noreply@github.com>
```

Placed **before** `Changelog:` in the footer section.

---

## Refs

Optional references to related issues, PRs, or documentation:

```
Refs: #241, #243
Refs: https://github.com/sukria/koan/pull/250
```

Placed after `Co-Authored-By:` and before `Changelog:`.

---

## Changelog

**Mandatory for every commit.** This is non-negotiable.

The Changelog footer captures customer-facing changes for release notes generation. It is **always the last line** of the commit message.

### With content (customer-visible change)

```
Changelog: Add Slack as an alternative messaging provider
Changelog: Fix mission picker ignoring paused state
```

### Empty (no customer-facing impact)

```
Changelog:
```

An empty Changelog is perfectly valid for internal changes (refactors, tests, CI, docs). Prefer an empty Changelog over a missing one.

---

## Complete Examples

### Feature with JIRA case

```
feat(bridge): add Slack messaging provider

Case KOAN-242:

Implement SlackProvider as an alternative to TelegramProvider. Uses the
Slack Web API with bot tokens for message delivery. Configuration via
config.yaml `messaging.provider: slack` with `SLACK_BOT_TOKEN` env var.

Supports channel-based messaging, thread replies, and file uploads.
Falls back to direct messages when channel is not configured.

Co-Authored-By: Claude <noreply@anthropic.com>
Changelog: Add Slack as an alternative messaging provider
```

### Bug fix with GitHub issue

```
fix(missions): prevent duplicate entries on concurrent writes

Case #198:

Race condition in insert_mission() when bridge and runner both write to
missions.md simultaneously. Added fcntl.flock() file locking around the
read-modify-write cycle.

Changelog: Fix duplicate mission entries when sending multiple commands rapidly
```

### Refactor (no customer impact)

```
refactor(run): replace 5 subprocess calls with direct imports

Case N/A:

Replace subprocess.run() calls to Python scripts with direct function
imports. Eliminates 5 shell invocations per iteration, reducing startup
overhead and improving error propagation.

Modules affected: pick_mission, contemplative_runner, usage_tracker,
recurring_scheduler, recover.

Changelog:
```

### Documentation

```
docs: add LLM provider setup guides for Claude, Copilot, and Local LLM

Case KOAN-230:

Create provider-specific setup documentation in docs/ with step-by-step
instructions for each supported CLI provider.

Files added:
- docs/provider-claude.md
- docs/provider-copilot.md
- docs/provider-local.md

Refs: #229
Changelog:
```

### Test addition

```
test(github): comprehensive tests for notification-driven commands

Case KOAN-251:

Add 41 tests covering the full GitHub notification → mission pipeline:
notification fetching, @mention parsing, permission checks, reaction
deduplication, and command-to-mission conversion.

Changelog:
```

### Fix with multiple case IDs

```
fix(plan): strip CLI preamble noise and title artifacts from plan output

Case KOAN-248, #248:

The plan command was including CLI preamble output (directory listings,
tool invocations) in the GitHub issue body. Added strip_preamble() to
remove everything before the actual plan content.

Also fixes issue title generation — was using raw first line instead of
extracting a meaningful summary.

Co-Authored-By: Claude <noreply@anthropic.com>
Changelog: Fix noisy plan output in GitHub issues
```

### Chore / CI

```
ci: add Python 3.14 to test matrix

Case N/A:

Add Python 3.14-dev to the GitHub Actions CI matrix with
allow-prereleases flag. No breaking changes detected.

Changelog:
```

### Revert

```
revert: feat(bridge): add WebSocket support

Case KOAN-300:

Revert commit a1b2c3d. WebSocket support caused memory leaks under
sustained load. Reverting to HTTP polling until the issue is resolved.

Refs: #299
Changelog: Remove experimental WebSocket support (stability issues)
```

---

## Special Cases

### Merge commits

Auto-generated by GitHub (`Merge pull request #241 from branch`). These are **exempt** from the full format — the standard GitHub merge message is acceptable.

### Multi-scope commits

If a commit genuinely touches multiple areas, either:
1. Use comma-separated scopes: `feat(bridge,runner): ...`
2. Pick the primary scope
3. Consider splitting the commit

### Long descriptions

If the body exceeds ~300 words, the commit is probably too large. Consider splitting it into smaller, focused commits.

### Case ID placement

Case IDs go in the **body only**, never in the subject line.

Good: `feat(bridge): add restart support`
Bad: `feat(bridge): KOAN-123 add restart support`

---

## Template

Quick copy-paste template:

```
<type>(<scope>): <subject>

Case <PROJ-123 or #123 or N/A>:

<description of what changed and why>

Changelog: <customer-facing change or empty>
```

Optional footers (when applicable):
```
Co-Authored-By: <name> <<email>>
Refs: <issue or PR links>
```

---

## Guidelines for AI Agents

When Kōan or other AI agents write commit messages:

1. **Extract case ID from mission context.** If the mission references a JIRA ticket or GitHub issue, use it. If not, write `Case N/A:`.
2. **Never invent case IDs.** A fake `Case KOAN-999:` is worse than `Case N/A:`.
3. **Always include Changelog.** Even if empty. This is mandatory.
4. **Keep subjects semantic.** Describe the change in human terms, not implementation details.
5. **Use Co-Authored-By.** AI-assisted commits should credit the AI tool used.
6. **Match the scope to the module.** Use the scope table above for consistency.
7. **One commit = one logical change.** Don't bundle cleanup with features.

---

## Optional Enforcement Tooling

The Kōan project uses documentation-based conventions. The tools below are **optional** — for teams that want automated enforcement.

### commitlint

[commitlint](https://commitlint.js.org/) validates commit messages against Conventional Commits.

Example `.commitlintrc.yaml`:

```yaml
extends: ['@commitlint/config-conventional']
rules:
  body-max-line-length: [2, always, 100]
  footer-max-line-length: [2, always, 100]
  subject-case: [2, always, [sentence-case, lower-case]]
  type-enum:
    - 2
    - always
    - [feat, fix, docs, refactor, test, chore, perf, ci, build, revert]
```

### husky (git hooks)

[husky](https://typicode.github.io/husky/) sets up git hooks for automated checks:

```bash
# Install (Node.js projects only)
npm install --save-dev @commitlint/cli @commitlint/config-conventional husky
npx husky init
echo 'npx --no -- commitlint --edit "$1"' > .husky/commit-msg
```

### GitHub Actions (CI validation)

```yaml
# .github/workflows/commitlint.yml
name: Commit Lint
on: [pull_request]
jobs:
  commitlint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: wagoid/commitlint-github-action@v6
```

> **Note:** These tools require Node.js dependencies. For a Python project like Kōan, consider enabling only in CI (GitHub Actions) rather than locally.

---

*This document is the single source of truth for commit message format. When in doubt, refer here.*

*See also: [CONTRIBUTING.md](../CONTRIBUTING.md) · [CLAUDE.md](../CLAUDE.md)*
