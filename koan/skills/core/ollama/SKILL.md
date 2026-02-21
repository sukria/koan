---
name: ollama
scope: core
description: Ollama server status, model listing, pulling, and removal
version: 1.2.0
audience: bridge
worker: true
commands:
  - name: ollama
    description: "Ollama management: /ollama [list|pull|remove] — show status, list/pull/remove models."
    aliases: [llama]
handler: handler.py
---
