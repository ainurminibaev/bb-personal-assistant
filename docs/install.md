# Installing an assistant or an employee

Run the installer from the extracted platform release, on the BB host, with the BB CLI on `PATH`. A clean server is
prepared first with `deploy/server-setup.sh`.

## Personal assistant

```
python3 setup.py --machine <BB machine ID> --workspace <private folder> --language en
```

Then pair the owner with the bot as described in [README.md](../README.md).

## Employee from an organization repository

```
python3 setup.py --machine <BB machine ID> --organization <git URL> --employee <name> \
  --workspace <folder for the copy> --provider codex
```

- The copy contains the root files, `shared/` and `employees/<name>/` only; the BB project is created in
  `employees/<name>` and named after the employee (or `--name`).
- The installer prints one line per difference between `employee.yml` / `organization.yml` and the installation.
- Adding the next employee: a folder in `employees/`, its name in `organization.yml`, push, and the same command with
  the new name and its own `--workspace`.

## Employee with its own repository

```
python3 setup.py --machine <BB machine ID> --kind employee --workspace-repo <git URL> --workspace <empty folder>
```

This is the single-employee case and keeps working.

## Update

Run the same command again. With `--organization`, the copy is updated with `git pull --ff-only`, skill links are
refreshed and the differences are printed again; a change in `shared/` reaches the employee this way. If the employee
has local commits that are not pushed, the installer stops and asks to push them first.

## Release candidates and mirrors

The installer expects the platform and the bridge from their release sources. To install from an already installed
release candidate or a local mirror, add `--keep-installed-sources`: the installed sources are kept as they are and only
their enabled state is checked.

## Rollback

Platform: reinstall the previous release through BB Settings → Updates. Organization copy:
`git -C <copy> reset --hard <previous commit>` and the same setup command. Memory, conversations and sign-ins are not in
the copy and are not touched by either.

## Tools

On the first run the installer sets up every line of `tools.yml` that this installation gets (pictures, context-mode,
the rules-compatibility skill and hook, the browser, the agent's own mail skill) and prints the state of each line:
installed, excluded, needs_login, needs_setup or error, with what to do. Nothing is skipped silently. The same later:
`python3 setup.py tools status [--json] --workspace <BB project folder>`. A plugin that this server cannot read from
its source can be installed from a copy: `--tool-source <id>=<path or git source>`. Format and levels:
[tools.md](tools.md).

An assistant gets the knowledge base plugin; an employee does not. An assistant's BB also tells every thread how to
reach the owner through the active assistant conversation (`ownerContact` setting of the platform plugin).
