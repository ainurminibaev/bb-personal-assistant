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
- `cli` — a command from the platform package (`path`) into the account's program folder (on the PATH of its BB),
  with the programs it needs: `requires` from system packages, `download` — a pinned release checked by `sha256`
  per architecture. The credentials store (`secrets-store`: `vallis-secret`, sops, age) is such a line.
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
secrets:                              # the credentials store (С37): names and purpose only, never values
  admin: [age1…]                      # administrators' age public keys: they can decrypt what agents keep
  keys:
    TASKS_TOKEN: {what: ключ трекера задач, to: [support]}    # or to: all
    AGENTMAIL_API_KEY: {what: ключ почтового сервиса, to: all}

# employees/<name>/employee.yml — the same two sections, for this employee only
tools:
  add: [image-gen]                    # an employee's line wins over the organization's exclusion
mcp:
  crm: {url: https://crm.example.com/mcp, token_env: CRM_TOKEN}
secrets:
  keys:
    CRM_TOKEN: {what: ключ CRM}
```

**Keys.** A described key's value lives in the account's store (`vallis-secret`, see `deploy/secrets-store.md`).
An organization key is made by the administrator for the named employees' public keys
(`vallis-secret grant NAME --to <recipients> --out <file>`), the file is brought to the employee's server and taken
with `vallis-secret receive <file>`; an employee's own key is asked with `vallis-secret ask NAME`. When `token_env` of an
MCP connection names a described key, Claude Code takes it from the store (`headersHelper`), Codex still reads the
variable of the BB service. The installer puts the administrators' keys into `~/.config/vallis/admin.recipients`
and a pre-commit check of the workspace's git copy that refuses a commit with a stored value
(`vallis-secret leaks --git-staged`).

An extra connection on the owner's request is a line in `employee.yml` and a new run of the installer. The installer
writes the employee's MCP servers into `.mcp.json` (Claude Code) and `.codex/config.toml` (Codex) of the employee folder;
a file kept in git is not changed and the line says so.

## Spaces

Tools are what the agent uses; spaces are where it works (tracker, knowledge, drive, chats, repositories, mail).
A space only refers to what is described elsewhere — no addresses, keys or values here:

```yaml
# organization.yml
spaces:
  tasks:
    what: задачник команды
    kind: tracker                       # tracker | knowledge | drive | chats | repositories | mail | other
    via: mcp:tracker                    # mcp:<server> | tool:<tools.yml line> | connections:<name> | folder:<path> | groups
    to: all                             # or [employees]
    access: write                       # read | write: what was agreed; the check never writes to the system
    map: shared/knowledge/where.md#задачник   # where "what lies where" is described
# employees/<name>/employee.yml
spaces:
  exclude: [drive]                      # an organization space this employee does not get
  chats: {what: чаты и группы, kind: chats, via: groups, access: write}
```

A space is `ready` when what `via` refers to is installed (groups: the employee has groups) and its `map` file is
there; otherwise it takes the state of that line (`needs_login`, `needs_setup`, `error`) or `needs_setup` with
the reason. An excluded space is not listed. `what` and `reason` are words without paths or addresses.

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

`"accesses"` lists the agent's own credentials from its store (`vallis-secret list`): `name`, `what`, `level`,
`source`, `state` — never values. It is what the agent answers «what accesses do you have» with (С43).

`"spaces"` lists the spaces of the installation (`id`, `what`, `kind`, `level`, `via`, `access`, `map`, `state`,
`reason`, `action`); empty for an installation without a description.

Every line of `tools.yml` is listed, then every MCP connection of the description, then every described key
(`secret:<NAME>`: `installed` — in the store and decryptable, `needs_setup` — not there yet, `error` — does not open). `state` is one of:

| state | meaning |
|---|---|
| `installed` | set up and working as far as the installer can check |
| `excluded` | not for this kind of installation, not added, or excluded by the organization or the employee |
| `needs_login` | installed, a login or a key is missing |
| `needs_setup` | not installed yet or needs a setting; `action` says what to do |
| `error` | the last installation attempt failed or a check found a fault; `reason` says why |

`problems` are mistakes in the description (unknown names, values instead of variable names). The installer prints the
same list as text at the end of every run.
