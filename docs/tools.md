# Tools

`tools.yml` is the list of general platform tools: what every installation gets. The installer sets each line up on
the first run, skips nothing silently and prints the state of every line at the end. The same state is one command
later, in a stable machine-readable form for a future interface.

## Levels

| Level | Where it is described | Who gets it |
|---|---|---|
| `platform` | `tools.yml` of the platform | every installation of the kinds in `installs`, unless excluded |
| `organization` | `organization.yml` of the organization repository | named employees |
| `employee` | `employees/<name>/employee.yml` | one employee |

The owner's personal tools (the owner's Telegram account, browser, computer, private MCP, long recordings) are not part of
the platform and are not in this list.

## tools.yml

```yaml
tools:
  image-gen:
    what: Picture generation and editing at any agent model
    kind: plugin                      # plugin | npm | skill | hook | service
    source: git:https://github.com/<owner>/bb-config.git@^0.1.0
    subdirectory: plugins/image-gen   # plugin in a folder of a shared repository
    tag_prefix: image-gen/            # with tags <id>/vX.Y.Z
    installs: [assistant, employee, agents]
    login: Codex login of the account that runs BB
    login_check: ~/.codex/auth.json
```

- `core: true` — the base of the installation (the bridge, the platform plugin); it cannot be excluded.
- `default` — `true` (omitted) or the list of kinds that get the line without `tools.add`.
- `plugin` — `bb plugin install <source>`, updated by BB itself; `builtin:<id>` for BB's own catalog.
- `npm` — `npm install -g <package>@<version>`, then the package's own `setup` for the agents found (Codex, Claude Code).
- `skill` — a skill folder from a git tag into `~/.agents/skills` and `~/.claude/skills` of the agent's account;
  `plugin: <id>` — the skill comes with that plugin.
- `hook` — a hook script from a git tag, installed by its own `--apply` into the account's agent settings.
- `service` — a service of the server, one per server outside any BB (speech, the local Telegram server). The services
  installer puts it; the list only shows its state (`check_commands`, `check_units`, the service's own `state_file`).
- `setting` — what has to be configured after installation (`env` variable or `file` in the installation's closed
  folder); without it the line is `needs_setup`.

## Organization and employee

```yaml
# organization.yml
tools:
  exclude: [image-gen]                # general lines this organization does not want
  add: []
mcp:                                  # MCP connections: address and the name of the key variable, no values
  tasks:
    url: https://tasks.example.com/mcp
    token_env: TASKS_TOKEN            # set in the environment of the employee's BB service
    employees: [support]              # or: all

# employees/<name>/employee.yml — the same two sections, for this employee only
tools:
  add: [image-gen]                    # an employee's line wins over the organization's exclusion
mcp:
  crm: {url: https://crm.example.com/mcp, token_env: CRM_TOKEN}
```

An extra connection on the owner's request is a line in `employee.yml` and a new run of the installer. The installer
writes the employee's MCP servers into `.mcp.json` (Claude Code) and `.codex/config.toml` (Codex) of the employee folder;
a file kept in git is not changed and the line says so.

## State

```
python3 setup.py tools status [--json] [--workspace <BB project folder>]        assistant or employee
python3 tools.py status [--json] --kind agents --account <agent> \
    --bb-user <account of the agents' BB> --bb-cli <bb> --bb-url <url>           an agent of the agents' BB (root)
```

`--json` prints (format 1; new fields may be added, existing ones keep their meaning):

```json
{
  "format": 1,
  "kind": "employee",
  "for": "support",
  "tools": [
    {"id": "image-gen", "what": "…", "kind": "plugin", "level": "platform",
     "state": "needs_login", "reason": "no login: …", "action": "log in the account that runs BB"},
    {"id": "mcp:tasks", "what": "https://tasks.example.com/mcp", "kind": "mcp", "level": "organization",
     "state": "needs_login", "reason": "the key is read from TASKS_TOKEN", "action": "set TASKS_TOKEN …"}
  ],
  "problems": ["organization.yml tools.exclude: telegram-projects is the base of the installation and stays"]
}
```

Every line of `tools.yml` is listed, then every MCP connection of the description. `state` is one of:

| state | meaning |
|---|---|
| `installed` | set up and working as far as the installer can check |
| `excluded` | not for this kind of installation, not added, or excluded by the organization or the employee |
| `needs_login` | installed, a login or a key is missing |
| `needs_setup` | not installed yet or needs a setting; `action` says what to do |
| `error` | the last installation attempt failed or a check found a fault; `reason` says why |

`problems` are mistakes in the description (unknown names, values instead of variable names). The installer prints the
same list as text at the end of every run.
