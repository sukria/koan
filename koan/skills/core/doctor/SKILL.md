---
name: doctor
scope: core
description: Diagnose system health and optionally fix common issues
version: 1.0.0
audience: bridge
commands:
  - name: doctor
    description: Run health diagnostics on the Koan installation
    usage: /doctor [--fix]
    aliases: [diag, diagnose]
handler: handler.py
---
