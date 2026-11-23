# Organization repository

One private repository describes an organization and each of its employees. It holds descriptions, rules and skills
only: no platform code and no secrets.

```
organization.yml          the organization
AGENTS.md -> shared/AGENTS.md
CLAUDE.md                 one line: @AGENTS.md
shared/
  AGENTS.md               rules for all employees of the organization
  skills/<skill>/SKILL.md skills for all employees
employees/
  <name>/
    employee.yml          this employee
    AGENTS.md             role and rules (CLAUDE.md: one line @AGENTS.md)
    skills/<skill>/       own skills
    plans/, knowledge/    working documents of the team
RUN.md                    how employees are installed, updated and rolled back; where secrets live (names only)
```

## organization.yml

```yaml
name: Example Organization
timezone: Europe/Berlin       # IANA time zone, default for every employee
language: en
admin: github-login           # administrator: edits shared/ and this file
platform: "0.3"               # platform version the employees run ("0.3" matches any 0.3.x)
employees:                    # every folder in employees/ that is installed
  - support
  - sales
services:                     # shared services: addresses only, no keys
  tasks: https://tasks.example.com
connections:                  # shared connections: where they are, no values
  github: example-org
tools:                        # general platform tools: exclude or add lines of tools.yml (docs/tools.md)
  exclude: []
mcp:                          # MCP connections for named employees: url, token_env, employees (docs/tools.md)
  tasks: {url: https://tasks.example.com/mcp, token_env: TASKS_TOKEN, employees: [support]}
```

## employee.yml

```yaml
kind: employee                # installation kind: employee or assistant
description: Support agent of the team
machine: support-server       # BB machine name or ID
bot: example_support_bot      # Telegram bot username
provider: codex               # codex or claude-code
model: <model ID>
timezone: Europe/Berlin       # optional, overrides the organization's
groups: [-1001234567890]      # Telegram group chats the employee works in
quiet_hours: "21:00-09:00"    # reserved for task tracking
reminders: {}                 # reserved for task tracking
tools: {add: [], exclude: []} # general platform tools for this employee (docs/tools.md)
mcp: {}                       # this employee's MCP connections: url and token_env, no values (docs/tools.md)
```

Every field is optional. The installer compares the fields it can read from BB with the installation and prints one
line per difference: kind, machine, bot, provider and model (of the latest thread in the employee's project), time zone,
platform version, groups (when the bridge reports chat IDs). Missing role files, a missing `employees` entry and unknown
fields are reported the same way. Fields marked "reserved" are listed as not compared.

## How shared rules and skills reach an employee

- The installer makes a sparse git copy: root files, `shared/` and `employees/<name>/` only. The BB project folder is
  `employees/<name>`.
- Codex reads `AGENTS.md` from the repository root down to the project folder; Claude Code reads `CLAUDE.md` from the
  project folder up. The root `AGENTS.md` links to `shared/AGENTS.md`, so both agents get the shared rules together
  with the employee's own `AGENTS.md`.
- Skills of `shared/skills/` and `employees/<name>/skills/` are linked into `.claude/skills/` and `.agents/skills/` of
  the employee folder. An employee's own skill wins over a shared one with the same name. Links follow every update.
- Installation files (`local/`, `projects.md`, hooks and agent settings written by the installer, the skill links) are
  excluded in the copy's `.git/info/exclude`, so the employee's `git status` shows only real changes.

## Who sees what

- Other employees' folders are not checked out, and with a remote repository their contents are not downloaded
  (partial clone). An agent reading files does not find them.
- The organization repository is not an access boundary; closed documents belong in a separate repository with
  access by list.
- `shared/` is edited by the administrator. To enforce this, protect the default branch and add a `CODEOWNERS` entry
  for `shared/` and `organization.yml`.
