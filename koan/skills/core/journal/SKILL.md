---
name: journal
scope: core
description: View journal entries
version: 1.0.0
commands:
  - name: log
    description: Show latest journal entry
    usage: /log [project], /log [project] [date]
    aliases: [journal]
handler: handler.py
---
