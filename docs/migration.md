# Moving an employee into an organization repository

From "one repository per employee" to "one repository per organization", without stopping the employee and without
losing history.

## 1. Build the organization repository

1. Clone the employee repository and move everything into `employees/<name>/` with one `git mv` commit. Original
   commits stay as they are; `git log --follow` shows the history of every file.
2. Add `organization.yml`, `employees/<name>/employee.yml`, `shared/AGENTS.md`, `shared/skills/`, the root
   `AGENTS.md` link and `CLAUDE.md`, `RUN.md`. Move rules that apply to every employee into `shared/AGENTS.md` only
   when they really are common.
3. Check that no secrets are in the history, create the private organization repository and push.

## 2. Try it on a test installation

Install the employee from the organization repository on a test BB (`--organization ... --employee <name>`), run the
acceptance scenarios, change something in `shared/` and update, add a second test employee.

## 3. Switch the working employee

1. Copy: the BB data folder and the current workspace (archive with permissions), and note the current project IDs,
   chat bindings and plugin versions.
2. Make the organization copy next to the current workspace with the installer (a new `--workspace`).
3. Move the working state that is not in git (`local/`, `projects.md`, untracked files the employee relies on) from
   the old workspace into `employees/<name>/` of the copy.
4. Point the employee's existing BB project at the new folder (`bb project source` or the project settings), so
   threads, chat bindings and memory stay with the same project.
5. Run the installer again: no differences except the expected ones; smoke scenarios pass in the working chat.
6. Rollback: point the project back at the old workspace (it is left untouched) and restart BB if it was restarted.

## 4. Archive the old repository

After a week of normal work: a line at the top of its README pointing to the new place, then archive it. Do not
delete it.
