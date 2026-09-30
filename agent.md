# Agent Behavioral Guidelines

## 1. Think Before Coding
- Never make unstated assumptions. Surface ambiguities, trade-offs, and edge cases.
- If requirements are unclear, ask clarifying questions before editing files.
- If multiple valid approaches exist, briefly outline options instead of picking one arbitrarily.
- If a simpler design exists, challenge the approach and propose it.

## 2. Simplicity First
- Implement the minimal, idiomatic solution that directly solves the problem.
- No speculative features, premature abstractions, or over-engineering for hypothetical futures.
- Do not add flexible configuration or layered abstractions for single-use logic.
- Self-check: "Would a senior engineer consider this unnecessarily complex?"

## 3. Surgical Changes
- Touch only what is strictly necessary; keep diffs clean and minimal.
- Do NOT refactor, rename, format, or "clean up" adjacent code or comments unless explicitly requested.
- Preserve existing coding styles, patterns, and naming conventions.
- If you notice unrelated dead code or bugs, point them out in text; do not touch them.
- Self-check: "Does every modified line directly trace back to the user's prompt?"

## 4. Goal-Driven Execution
- Define clear verification criteria before modifying code.
- Prefer writing or identifying a reproducing test/check before implementing fixes.
- For multi-step tasks, follow a tight cycle: state the step -> execute -> verify before proceeding.
- Do not stop at code generation; run relevant tests and linters to verify the fix works.

## Read prd.md, trd.md to understand goal.