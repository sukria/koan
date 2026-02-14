# Token Requirements for sukria-koan0

The GitHub account `sukria-koan0` uses a **classic Personal Access Token** (PAT).

## Current Status (2026-02-14)

Token is fully operational. Profile deployed.

### Token scopes (classic PAT)

`notifications`, `project`, `read:org`, `repo`, `user`, `workflow`, `write:discussion`

### What works

| Feature | Method | Status |
|---|---|---|
| Profile update (name, bio, company, location) | `PATCH /user` via GH_TOKEN | OK |
| Profile repo creation | `POST /user/repos` via GH_TOKEN | OK |
| Profile README push | `PUT /repos/.../contents/` via GH_TOKEN | OK |
| Git push to sukria/koan branches | SSH (git@github.com) | OK |
| Issues (create/update) | GH_TOKEN | OK |
| Pull requests (create/read) | GH_TOKEN | OK |

### Notes

- Git push uses SSH, not the GH_TOKEN. The `repo` scope on the classic PAT
  grants API access but push permissions come from SSH keys + collaborator status.
- The `user` scope grants profile write access on classic PATs (no separate `profile=write` needed).

## Verification

```bash
# Check permissions
bash github-profile/check-permissions.sh

# Deploy/update profile
bash github-profile/setup-profile.sh
```
