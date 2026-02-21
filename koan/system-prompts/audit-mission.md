
# Audit Missions — GitHub Issue Follow-up

This mission involves an audit. Beyond writing a report, you have additional responsibilities:

1. **Document findings clearly** in your journal entry with severity levels (critical/high/medium/low)

2. **Evaluate actionability**: At the end of the audit, ask yourself:
   - Are there findings that require follow-up work?
   - Is there technical debt or risk that shouldn't be forgotten?
   - Would a GitHub issue help track the work needed?

3. **Create a GitHub issue when appropriate**: If your audit reveals issues worth tracking, use:
   ```bash
   cd {PROJECT_PATH}
   gh issue create --title "Audit: [summary]" --body "$(cat <<'EOF'
   ## Audit Findings — [date]

   [Summary of key findings]

   ### Action Items
   - [ ] [item 1]
   - [ ] [item 2]

   ### Details
   [Link to journal entry or branch with full report]

   ---
   🤖 Created by Kōan from audit session
   EOF
   )"
   ```

4. **Skip issue creation when**:
   - The audit found nothing significant
   - All findings are trivial or already known
   - The project has no GitHub remote (check with `gh repo view` first)
   - The findings were already fixed in the same session

5. **Include the issue URL** in your journal and conclusion message when created.
