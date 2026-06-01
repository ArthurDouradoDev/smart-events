# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:

1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]


Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

## 5. Memory & Process Documentation

**Maintain living project memory. Never repeat decisions or mistakes.**

Claude forgets everything between sessions. You must maintain two persistent files in the project root:

- **MEMORY.md**: Decision log + permanent facts (architecture choices, constraints, domain rules, tech stack locks, non-obvious facts).
- **ERRORS.md**: Failure log (what broke, root cause, lesson learned, prevention rule).

**Update rules (do this automatically):**
- After any important decision, feature, or refactor: append to MEMORY.md with date + rationale.
- After every mistake or failed approach: append to ERRORS.md with clear root cause and fix.
- At end of session or complex task: write a short session summary in MEMORY.md.
- Before starting any complex task: read current MEMORY.md first.
- Keep entries concise, dated, and actionable. Never delete history.

**Lock your tech stack:** Never suggest new frameworks, tools, or patterns unless explicitly asked. Reference MEMORY.md for what is already chosen.

These files create continuity across sessions and prevent re-proposing rejected solutions.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, clarifying questions come before implementation, and the project has clear, living documentation of its decisions and lessons.