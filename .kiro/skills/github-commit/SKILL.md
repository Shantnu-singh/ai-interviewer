---
name: github-commit
description: Review all uncommitted files, generate a commit message for each, and commit them one file per commit backdated across the last 7 days. Use for personal projects to spread commit history evenly over the past week.
---

# GitHub Commit (backdated, one commit per file)

You are helping commit uncommitted changes in a **personal project** repository.
The goal: make the GitHub contribution/commit history easier to track by committing
each changed file as its own commit, spread evenly over the **last 15 days** using
git's `--date` feature.

$ARGUMENTS

## Allowed commands (STRICT)

Only these git commands may be used. Do NOT run anything else (no `git push`,
no `git reset`, no `git rebase`, no `git checkout`, no shell file manipulation):

- `git status`
- `git add <file>`
- `git commit --date=<date> -m "<message>"`

If any step would require a command outside this list, STOP and ask the user.

## Procedure

1. **Discover changes.** Run `git status --porcelain` to list every uncommitted
   file (modified, added, untracked, deleted, renamed). Build the full list.

2. **Report the plan.** Show the user the list of files you found and the number
   of commits that will be created (one per file). Briefly confirm before writing
   any commits.

3. **Generate commit messages.** For each file, read it (or its diff) enough to
   write a concise, conventional commit message describing that file's change.
   Use conventional-commit style where it fits, e.g.:
   - `feat: add <thing> in <file>`
   - `fix: correct <issue> in <file>`
   - `docs: update <file>`
   - `style: adjust formatting in <file>`
   - `chore: update <file>`
   Keep the subject under ~70 characters. Keep messages truthful to the actual
   change — do not fabricate.

4. **Compute backdated timestamps.** Spread the commits evenly across the last
   15 days (from 15 days ago up to now), oldest file first. For N files, place each
   commit at an evenly spaced point in that window so the history looks natural.
   - Use ISO 8601 format for `--date`, e.g. `2026-09-28T14:30:00`.
   - Make timestamps strictly increasing (each commit later than the previous)
     so git log ordering stays sensible.
   - Vary the time-of-day a little so they are not all at midnight.

5. **Commit each file individually.** For every file, in chronological order:
   ```
   git add "<file>"
   git commit --date="<computed-date>" -m "<generated message>"
   ```
   Stage and commit ONE file per commit so each change maps to exactly one commit.

6. **Verify.** Run `git status` at the end to confirm the working tree is clean
   (nothing left uncommitted), and summarize how many commits were created and the
   date range they span.

## Notes

- This only creates local commits. It does **not** push. Pushing is out of scope
  for this skill — the user pushes manually when ready.
- `--date` sets the **author date**. The committer date will still be "now" unless
  the user has set `GIT_COMMITTER_DATE`; GitHub's contribution graph uses the
  author date, so backdating `--date` is sufficient for the stated goal.
- If there are no uncommitted files, report that and stop.
- If a file is deleted, use `git add "<file>"` (which stages the deletion) then
  commit as normal with a `chore: remove <file>` style message.
