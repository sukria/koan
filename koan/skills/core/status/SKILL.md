---
name: status
scope: core
description: Show Koan status, missions, and run loop health
version: 1.0.0
commands:
  - name: status
    description: Quick status overview
    aliases: [st]
  - name: ping
    description: Check if run loop is alive
    aliases: []
  - name: usage
    description: Detailed quota and progress
    aliases: []
handler: handler.py
---
