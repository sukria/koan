---
name: add-project
scope: core
description: Add a project from a GitHub URL
version: 1.0.0
audience: bridge
worker: true
commands:
  - name: add-project
    description: Clone a GitHub repo and add it to the workspace
    usage: /add-project <github-url> [name]
    aliases: []
handler: handler.py
---
