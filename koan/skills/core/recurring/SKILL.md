---
name: recurring
scope: core
description: Manage recurring missions (hourly, daily, weekly)
version: 1.0.0
commands:
  - name: daily
    description: Add a daily recurring mission
    usage: /daily <text> [project:<name>]
  - name: hourly
    description: Add an hourly recurring mission
    usage: /hourly <text> [project:<name>]
  - name: weekly
    description: Add a weekly recurring mission
    usage: /weekly <text> [project:<name>]
  - name: recurring
    description: List all recurring missions
    usage: /recurring
  - name: cancel-recurring
    description: Cancel a recurring mission
    usage: /cancel-recurring <n>, /cancel-recurring <keyword>
handler: handler.py
---
