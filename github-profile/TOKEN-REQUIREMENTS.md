# Token Requirements for sukria-koan0

The GitHub account `sukria-koan0` uses a **fine-grained Personal Access Token**.

## Current Status (2026-02-14)

Token authenticates correctly as `sukria-koan0` but lacks critical permissions.

### Missing permissions

| Permission | Scope | Why |
|---|---|---|
| `profile=write` | Account | Update name, bio, company, location |
| `administration=write` | Account | Create profile repo (sukria-koan0/sukria-koan0) |
| `contents=write` | sukria/koan | Git push to koan/* branches |
| `issues=write` | sukria/koan | Create/update issues |
| `pull_requests=write` | sukria/koan | Create draft PRs |

### Working permissions

- Authentication (GET /user): OK
- Pull requests read (GET /repos/sukria/koan/pulls): OK
- Read repos: OK (5 repos visible)
- Push to sukria/Backup-Manager, sukria/zsh-aliases, Anantys/* repos: OK

## How to fix

1. Go to https://github.com/settings/personal-access-tokens
2. Find and edit the fine-grained PAT for sukria-koan0
3. **Account permissions**: set Profile to "Read and write"
4. **Repository access**: ensure `sukria/koan` is in the list (and all other Koan-managed repos)
5. **Repository permissions**:
   - Administration: Read and write
   - Contents: Read and write
   - Issues: Read and write
   - Pull requests: Read and write
   - Metadata: Read (auto-granted)
6. Run `bash github-profile/check-permissions.sh` to verify
7. Run `bash github-profile/setup-profile.sh` to deploy the profile

## Verification

```bash
# Quick check: all should pass
GH_TOKEN=<token> bash github-profile/check-permissions.sh

# Deploy profile
GH_TOKEN=<token> bash github-profile/setup-profile.sh
```
