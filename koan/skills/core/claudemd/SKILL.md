---
name: claudemd
scope: core
description: Refresh or create CLAUDE.md for a project based on recent architectural changes
version: 1.0.0
commands:
  - name: claude.md
    description: Refresh CLAUDE.md for a project
    usage: /claude.md <project-name>
    aliases: [claude, claudemd]
handler: handler.py
---
