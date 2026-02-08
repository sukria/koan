
# Verbose Mode (ACTIVE)

The human has activated verbose mode (/verbose). Every time you write a progress line
to pending.md, you MUST ALSO write the same line to {INSTANCE}/outbox.md so the human
gets real-time updates on Telegram. Use this pattern:

```bash
MSG="$(date +%H:%M) — description"
echo "$MSG" >> {INSTANCE}/journal/pending.md
echo "$MSG" >> {INSTANCE}/outbox.md
```

This replaces the single echo to pending.md. Do this for EVERY progress update.
The conclusion message at the end of the mission is still a single write as usual.
