# Code Rabbit — Proactive Self-Review

Before presenting ANY code change to the user, perform a silent self-review as if you were a senior code reviewer on the team.

## Review Checklist
For every code change, mentally verify:

### Correctness
- Does the logic actually do what was requested?
- Are edge cases handled (empty inputs, None/null values, missing files, network errors)?
- Are there off-by-one errors, wrong variable names, or copy-paste mistakes?

### Security
- No hardcoded secrets, API keys, or passwords in source code.
- User inputs are sanitized/validated before use.
- File paths are validated to prevent directory traversal.
- SQL/command injection vectors are eliminated.

### Performance
- No unnecessary loops, redundant API calls, or O(n²) where O(n) is possible.
- Large files/data are streamed, not loaded entirely into memory.
- Database/API calls are batched where possible.

### Maintainability
- Variable and function names are clear and descriptive.
- Functions do ONE thing. If a function is over 40 lines, consider splitting it.
- No dead code, unused imports, or commented-out blocks left behind.
- Error messages are helpful and actionable.

### Hsx-Specific
- Generated files go to `outputs/`, intermediate work to `scratch/`, never modify `uploads/`.
- Skills follow the `skill.json` + `skill.py` convention with a `run(**kwargs)` entry point.
- Server responses use the existing `_json()` helper pattern.
- Frontend changes are built with `npm run build` (tsc) and the output goes to `build/`.

## How to Apply
- Do NOT output the review as a separate section. Just silently fix any issues you find.
- If you catch a bug in your own code during review, fix it before presenting.
- If you notice a pre-existing issue in code you're touching, mention it briefly and fix it.
