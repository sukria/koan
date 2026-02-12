# Kōan Workspace

Place your projects here — either as directories or symlinks.

Kōan will auto-discover any project in this directory by its folder name.
No `projects.yaml` entry is required for workspace projects (but you can
add one if you need custom settings like model overrides or tool restrictions).

## Quick start

```bash
# Symlink an existing project
ln -s /path/to/your/project workspace/my-project

# Or move/clone directly
git clone https://github.com/you/my-project workspace/my-project
```

Kōan discovers projects at startup and when you run `/projects`.

## How it works

- Each immediate child directory becomes a project (name = directory name)
- Symlinks are resolved to their real paths
- Hidden directories (`.git`, `__pycache__`, etc.) are ignored
- If a project with the same name exists in `projects.yaml`, the yaml config
  takes precedence (the workspace path is ignored for that project)

## Customizing workspace projects

Workspace projects use global defaults. To customize, add an entry in
`projects.yaml` **without** the `path` field — the workspace path is used
automatically:

```yaml
projects:
  my-project:
    # No path needed — auto-detected from workspace/
    models:
      mission: "opus"
    git_auto_merge:
      enabled: true
```

## Limits

- Maximum 50 projects total (workspace + yaml combined)
- Broken symlinks are skipped with a warning
- Symlink loops are detected and skipped
- Non-directory files are ignored

## Symlink behavior

- Symlinks are automatically resolved to their real paths
- Symlinks can point anywhere on the filesystem (outside workspace/)
- Broken or circular symlinks are silently skipped
- The project name is the symlink name, not the target directory name
