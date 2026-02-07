---
name: check
scope: core
description: Check the status of a GitHub Pull Request or Issue and take appropriate action
version: 1.0.0
worker: true
commands:
  - name: check
    description: Check a PR/issue and take action if needed (rebase, review, plan)
    usage: /check https://github.com/owner/repo/pull/123
    aliases: [inspect]
handler: handler.py
---
