---
name: ai
scope: core
description: Queue an AI exploration mission for a project
version: 1.0.0
commands:
  - name: ai
    description: Queue an AI exploration mission for a project
    aliases: [ia]
    usage: |
      /ai [project]
      /ia [project]

      Queues a mission that explores a project in depth and suggests
      creative improvements. Unlike /magic (instant, lightweight),
      /ai runs as a full agent mission with access to the codebase.

      Examples:
        /ai         — explore a random project
        /ai koan    — explore the koan project
        /ia backend — explore the backend project
handler: handler.py
---
