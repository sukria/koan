---
name: update
scope: core
group: system
description: Update Kōan to latest upstream code and restart
version: 1.0.0
audience: bridge
commands:
  - name: update
    description: Pull latest code from upstream and restart both processes
    aliases: [upgrade]
    usage: "/update -- pull latest code and restart (alias: /upgrade)"
handler: handler.py
---
