---
name: language
scope: core
description: Set or reset reply language preference
version: 1.1.0
commands:
  - name: language
    description: Set reply language
    usage: /language <lang>, /language reset
    aliases: [lng]
  - name: french
    description: Switch replies to French
    usage: /french
    aliases: [fr, francais, français]
  - name: english
    description: Switch replies to English
    usage: /english
    aliases: [en, anglais]
handler: handler.py
---
