---
name: plan
scope: core
description: Deep-think an idea and create a GitHub issue with a structured plan
version: 1.0.0
worker: true
commands:
  - name: plan
    description: Plan an idea or iterate on an existing GitHub issue
    usage: /plan <idea>, /plan <project> <idea>, /plan <issue-url>
handler: handler.py
---
