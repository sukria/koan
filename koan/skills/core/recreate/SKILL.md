---
name: recreate
scope: core
description: "Recreate a diverged PR from scratch (ex: /recreate https://github.com/owner/repo/pull/42)"
version: 1.0.0
commands:
  - name: recreate
    description: "Recreate a diverged PR from scratch on current upstream (ex: /recreate https://github.com/owner/repo/pull/42)"
    aliases: [rc]
handler: handler.py
---
