---
name: pr
scope: core
description: Review and update a GitHub pull request
version: 1.0.0
commands:
  - name: pr
    description: Review and update a GitHub pull request
    usage: /pr <github-pr-url>
worker: true
handler: handler.py
---
