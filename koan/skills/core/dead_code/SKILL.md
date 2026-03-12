---
name: dead-code
scope: core
group: code
description: Scan a project for unused code (imports, functions, classes, dead branches)
version: 1.0.0
audience: hybrid
commands:
  - name: dead-code
    description: Scan a project for unused imports, functions, classes, and dead branches
    usage: /dead-code [project-name]
    aliases: [dc]
handler: handler.py
---
