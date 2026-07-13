# Copilot Instructions for This Repository

## Collaboration Style

- Keep communication direct and constructive, keeping in mind the overall goal of the project, not just the immediate code change.
- Avoid rushed "quick fixes" that add technical debt in the long run.

## Required Pre-Patch Workflow

Before applying any code patch or even ask for validating a command, always provide a short pre-patch brief with:

1. Problem summary: what is failing and where it appears.
2. Root cause hypothesis: why it is happening, based on evidence.
3. Proposed fix: the smallest safe change that addresses the cause.
4. Risk and scope: what could be affected and what is intentionally not changed.

Only when the user tells you to proceed, execute the patch or propose the command to run and wait for validation (this avoids the user having to press "skip" and block your workflow).

## Root-Cause Framing

- When the user asks "why did this happen now", explain the larger system/product-level change first, then the code-level mechanism.
- Do not start with low-level implementation details if they are consequences rather than the primary cause.
- Use this order: context change -> causal chain -> code detail.

## Command Prompt Hygiene

- Do not send terminal commands immediately after an explanation; let the user read first.
- Before each command batch, ask for explicit go-ahead (for example: "Run now?").
- Prefer short, single-purpose commands over long multi-line blocks.
- Avoid heredoc or large inline scripts unless the user explicitly asks for that format.
- If a command might generate a large Allow/Skip prompt, split it into smaller steps.

## Anti-Bloat Rule

- Prefer minimal, targeted edits over broad refactors unless these edits are clearly cheap fixes that hide an underlying architectural problem. In that case, don't hesitate to propose a better solution, but be prepared to justify it.
- If a temporary workaround needs to be used anyway, mark it as temporary and explain the follow-up needed.

## Validation Rule

After patching:

- Run the smallest relevant verification (compile/test/smoke check).
- Report results clearly, including anything not verified.
