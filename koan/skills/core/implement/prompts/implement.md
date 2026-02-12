You are implementing a plan from a GitHub issue. Your job is to read the plan carefully and execute it as code changes in the project.

## GitHub Issue

**Issue**: {ISSUE_URL}
**Title**: {ISSUE_TITLE}

## Plan to Implement

{PLAN}

## Additional Context

{CONTEXT}

## Instructions

1. **Read the plan carefully**: Understand the overall goal, the phases, and the acceptance criteria for each phase.

2. **Explore the codebase first**: Use Read, Glob, and Grep to understand the current state of the code. Verify that assumptions in the plan still hold — the codebase may have changed since the plan was written.

3. **Implement the changes**: Follow the plan's phases in order. For each phase:
   - Make the code changes described
   - Follow existing patterns and conventions in the codebase
   - Write tests if the plan calls for them
   - Ensure the phase's acceptance criteria ("Done when") are met

4. **Run existing tests**: After making changes, run the project's test suite to ensure nothing is broken. Fix any regressions.

5. **Be surgical**: Make the smallest changes necessary to fulfill the plan. Don't refactor unrelated code, don't add features not in the plan.

6. **Handle ambiguity**: If the plan is unclear about a detail, make your best judgment based on existing code patterns. Document your decision in a code comment if it's non-obvious.

7. **If the additional context specifies a subset** (e.g., "Phase 1 to 3"), only implement the specified phases. Skip the others.

Keep your changes focused, testable, and consistent with the project's existing style.
