---
name: idea
scope: core
description: Manage the ideas backlog
version: 1.0.0
commands:
  - name: idea
    description: Add or manage ideas in the backlog
    usage: /idea <text>, /idea promote <n>, /idea delete <n>
    aliases: [buffer]
  - name: ideas
    description: List all ideas in the backlog
handler: handler.py
---
