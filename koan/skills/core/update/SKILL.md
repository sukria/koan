---
name: update
scope: core
description: Update Koan to latest upstream code and restart
version: 1.0.0
commands:
  - name: update
    description: Pull latest code from upstream and restart both processes
    aliases: [upgrade]
    usage: "/update -- pull latest code and restart"
  - name: restart
    description: Restart both bridge and run loop
    aliases: []
    usage: "/restart -- restart processes without updating code"
handler: handler.py
---
