# Docker Setup

> Docker provides isolated environments and simplified deployment — ideal for
> VPS/server hosting or keeping Koan sandboxed on your machine. For running
> Koan directly (no container), see [INSTALL.md](../INSTALL.md).

## Prerequisites

- **Docker Engine 20+** and **Docker Compose v2+**
- **Claude authentication** (one of):
  - **ANTHROPIC_API_KEY** in `.env` — for API billing accounts ([console.anthropic.com](https://console.anthropic.com/settings/keys))
  - **Interactive login** — for Claude subscription users (browser-based, one-time)
- **GitHub CLI (`gh`)** authenticated on the host (for PR/issue operations)
- A messaging platform configured (**Telegram** or **Slack** — see [INSTALL.md](../INSTALL.md#2-set-up-a-messaging-platform))

## Quick Start

```bash
# 1. Clone and enter the repo
git clone https://github.com/sukria/koan.git
cd koan

# 2. Create your instance directory
cp -r instance.example instance

# 3. Set up credentials
cp env.example .env
# Edit .env — set messaging credentials (Telegram or Slack)
# For API billing: also set ANTHROPIC_API_KEY in .env

# 4. Add projects to the workspace
mkdir -p workspace
ln -s /path/to/your/project workspace/myproject

# 5. Run the setup script (auto-detects host paths, generates mounts)
./setup-docker.sh

# 6. Authenticate Claude CLI (pick one)
# Option A: API key — just set ANTHROPIC_API_KEY in .env (done in step 3)
# Option B: Interactive login (for Claude subscription users):
docker compose run --rm -it koan auth

# 7. Build and start
docker compose up --build
```

Verify it's running:

```bash
# Watch logs
docker compose logs -f

# Send a test message (requires messaging to be configured)
make say m="hello"
```

## Workspace Setup

Koan accesses your project repos through the `workspace/` directory. Each
subdirectory (or symlink) becomes a project the agent can work on.

```bash
mkdir -p workspace

# Symlink existing repos (recommended — no duplication)
ln -s ~/code/myapp workspace/myapp
ln -s ~/code/backend workspace/backend

# Or copy/clone directly
git clone https://github.com/you/myapp.git workspace/myapp
```

When you run `./setup-docker.sh`, it:

1. **Resolves symlinks** — Docker can't follow host symlinks, so the script
   creates explicit bind mounts for each symlinked project.
2. **Generates `projects.docker.yaml`** — maps each workspace entry to its
   container path (`/app/workspace/<name>`). This file is mounted as
   `projects.yaml` inside the container.
3. **Generates `docker-compose.override.yml`** — adds the resolved volume
   mounts and host UID/GID.

To add a new project later, symlink it into `workspace/` and re-run
`./setup-docker.sh`.

### Editing `projects.docker.yaml`

The generated file works out of the box, but you can edit it for per-project
overrides (auto-merge rules, CLI provider, etc.) — same schema as
`projects.yaml`:

```yaml
defaults:
  git_auto_merge:
    enabled: false
    base_branch: "main"
    strategy: "squash"

projects:
  myapp:
    path: /app/workspace/myapp
  backend:
    path: /app/workspace/backend
    git_auto_merge:
      base_branch: "staging"
```

## Architecture (How It Works)

### Container layout

The Docker image packages everything Koan needs: Python, Node.js, Claude CLI
(installed via npm), GitHub CLI (`gh`), and git. No host binaries are mounted.

Two processes run inside a single container, supervised by the entrypoint
script:

| Process | Purpose |
|---------|---------|
| `awake.py` | Telegram/Slack bridge — polls for messages, flushes outbox |
| `run.py` | Agent loop — picks missions, executes via Claude CLI |

If either process crashes, the entrypoint restarts it automatically.

### Authentication

- **Claude CLI** supports two auth methods:
  - **API key**: Set `ANTHROPIC_API_KEY` in `.env` (for API billing accounts).
  - **Interactive login**: Run `docker compose run --rm -it koan auth` to open a
    browser-based login flow (for Claude subscription users). Auth state persists
    in `claude-auth/` on the host, so login is a one-time process.
- **GitHub CLI** uses `~/.config/gh` mounted read-only from the host.
- **Git** uses a default identity (`Koan <koan@noreply.github.com>`), overridable
  by mounting `~/.gitconfig`.

### Volume mounts

| Host | Container | Purpose |
|------|-----------|---------|
| `./instance/` | `/app/instance/` | Runtime state (missions, memory, journal) |
| `instance/missions.docker.md` | `/app/instance/missions.md` | Isolated mission queue (see below) |
| `./workspace/` | `/app/workspace/` | Project repositories |
| `./logs/` | `/app/logs/` | Log files |
| `./claude-auth/` | `/home/koan/.claude/` | Claude CLI auth state (interactive login) |
| `projects.docker.yaml` | `/app/projects.yaml` | Project configuration |
| `~/.config/gh` | `/home/koan/.config/gh` | GitHub CLI auth (read-only) |

### Isolated mission queue

The container mounts `instance/missions.docker.md` over
`instance/missions.md`. This means Docker and a native (non-Docker) Koan
instance each have their own mission queue — they won't interfere if you run
both.

### Health check

The entrypoint writes a heartbeat timestamp to `.koan-heartbeat` every 5
seconds. Docker's `HEALTHCHECK` verifies it's less than 120 seconds old:

```bash
docker inspect --format='{{.State.Health.Status}}' koan
```

### UID/GID matching

To avoid permission issues on bind-mounted volumes, `setup-docker.sh` detects
your host UID/GID and passes them as build args. The container user runs with
the same IDs as your host user.

## Configuration

| File | Purpose |
|------|---------|
| `.env` | Credentials: `ANTHROPIC_API_KEY`, messaging tokens, `GITHUB_USER` |
| `projects.docker.yaml` | Project paths and per-project overrides (auto-generated) |
| `instance/config.yaml` | Agent settings (same as native setup) |
| `instance/soul.md` | Agent personality |
| `docker-compose.override.yml` | Volume mounts and build args (auto-generated) |

The two auto-generated files (`docker-compose.override.yml` and
`projects.docker.yaml`) are gitignored. Re-run `./setup-docker.sh` to
regenerate them after changing your workspace layout.

## Usage

### Make targets

```bash
make docker-setup   # Run setup-docker.sh
make docker-up      # Build and start (detached)
make docker-down    # Stop the container
make docker-logs    # Tail container logs
make docker-test    # Run the test suite inside the container
```

### Docker Compose commands

```bash
# Start in foreground (see logs directly)
docker compose up --build

# Authenticate Claude CLI interactively (one-time, for subscription users)
docker compose run --rm -it koan auth

# Interactive shell inside the container
docker compose run --rm koan shell

# Run tests
docker compose run --rm koan test

# Run only the agent loop (no bridge)
docker compose run --rm koan agent

# Run only the bridge (no agent)
docker compose run --rm koan bridge
```

### Dry-run setup

Preview what `setup-docker.sh` would generate without writing files:

```bash
./setup-docker.sh --dry-run
```

## Troubleshooting

### "Claude CLI is not authenticated"

The container needs one of:
- **API key**: Set `ANTHROPIC_API_KEY` in `.env` ([console.anthropic.com](https://console.anthropic.com/settings/keys))
- **Interactive login**: Run `docker compose run --rm -it koan auth` and follow the browser URL

### Permission errors on workspace files

UID/GID mismatch between host and container. Re-run `./setup-docker.sh` to
regenerate with the correct IDs, then rebuild:

```bash
./setup-docker.sh
docker compose up --build
```

### `gh` CLI not authenticated

The setup script mounts `~/.config/gh` from the host. Make sure `gh` is
authenticated on your host first:

```bash
gh auth status   # Should show a logged-in account
./setup-docker.sh  # Re-run to pick up the mount
```

### "projects section required" or no projects found

Re-run `./setup-docker.sh` to regenerate `projects.docker.yaml` from your
current `workspace/` layout.

### Container keeps restarting

Check the logs for the root cause:

```bash
docker compose logs --tail=50
```

Common causes: missing API key, invalid messaging credentials, no projects
configured.

### Docker and native Koan sharing state

Mission queues are isolated by default (`missions.docker.md` vs
`missions.md`). However, `instance/` state (memory, journal, config) is
shared. If you run both simultaneously, avoid editing `instance/config.yaml`
from both sides.
