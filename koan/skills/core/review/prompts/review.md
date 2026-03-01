# Code Review

You are performing a code review on a pull request. Your goal is to provide
actionable, constructive feedback that helps the author improve the code.

## Pull Request: {TITLE}

**Author**: @{AUTHOR}
**Branch**: `{BRANCH}` -> `{BASE}`

### PR Description

{BODY}

---

## Current Diff

```diff
{DIFF}
```

---

## Existing Reviews

{REVIEWS}

## Existing Comments

{REVIEW_COMMENTS}

{ISSUE_COMMENTS}

---

## Your Task

Analyze the code changes and produce a structured review. Focus on:

1. **Correctness** — Logic bugs, edge cases, off-by-one errors, race conditions
2. **Security** — Injection, authentication gaps, data exposure, unsafe operations
3. **Architecture** — Design issues, coupling, abstraction level, naming
4. **Maintainability** — Readability, complexity, test coverage gaps

### Rules

- Be specific: reference file names and line ranges from the diff.
- Prioritize: separate blocking issues from minor suggestions.
- Skip praise — focus on what needs attention.
- If the code is solid, say so briefly. Don't invent problems.
- Do NOT modify any files. This is a read-only review.

### Output Format

Structure your review as markdown with this exact format:

```
## PR Review — {title}

{one-sentence assessment of the PR and what needs to happen before merge}

---

### 🔴 Blocking

**1. Issue title** (`file_path`, `function_or_class`)
Description of the issue. Include code snippets when relevant.

### 🟡 Important

**1. Issue title** (`file_path`, `function_or_class`)
Description of the issue with suggested fix.

### 🟢 Suggestions

**1. Issue title** (`file_path`)
Description of the suggestion.

---

### Summary

Final assessment paragraph — what's good, what needs fixing, and whether
it's merge-ready after addressing the blocking items.
```

Rules for sections:
- Omit any severity section that has no items (don't include empty sections).
- Number items sequentially within each section.
- Use bold numbered titles: `**1. Title** (\`file\`, \`context\`)`
- Include code snippets in fenced blocks when they clarify the issue.
- The Summary section is always present.
