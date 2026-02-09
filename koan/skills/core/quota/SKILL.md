---
name: quota
scope: core
description: Check LLM quota live (no cache)
version: 1.0.0
commands:
  - name: quota
    description: Live quota and token usage metrics
    usage: /quota
    aliases: [q]
handler: handler.py
---
