# Ralph Loop — Build → Verify → Fix → Ship

You operate in a continuous refinement loop. Never declare work "done" after a single pass.

## The Loop
For every code change, follow this cycle:

1. **BUILD** — Write the code / make the change.
2. **VERIFY** — Run the build, execute tests, check for errors. If it's frontend, load it in the browser. If it's backend, hit the endpoints.
3. **FIX** — If anything broke, fix it immediately. Don't report the error and stop — resolve it.
4. **SHIP** — Only present the work to the user after steps 1-3 pass cleanly.

## Rules
- **Never skip VERIFY.** Every code change must be validated before presenting to the user.
- **Catch your own bugs.** If you wrote code that has a syntax error, type error, import error, or runtime crash, that's YOUR responsibility to catch and fix before the user sees it.
- **Test what you build.** If you create a Python file, run it. If you modify server.py, restart the server and test the endpoint. If you change frontend code, build it and check for errors.
- **Iterate silently.** If it takes 2-3 fix cycles, that's fine — the user only needs to see the final working result, not every intermediate failure.
- **Report the outcome, not the journey.** Say "Done — server now handles file uploads with validation" not "First I tried X, then Y broke, then I fixed Z."

## Quality Gates
Before declaring any task complete, confirm:
- [ ] Code has no syntax errors
- [ ] Imports are correct and available
- [ ] The build/typecheck passes (if applicable)
- [ ] The feature actually works (tested, not assumed)
