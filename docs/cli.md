# The `ac2` command line

`ac2` and `agentcraft2` are the same command. Every command writes working code with its
tests, which run with no account and no network. Run `ac2 --help` for the list and
`ac2 <command> --help` for every option of a command, with examples.

## A first session

```bash
ac2 init my-workspace --owner my-team   # create a workspace
cd my-workspace
ac2 new mcp people                      # an MCP server over sample data
ac2 new agent helper --mcp people       # an agent that may call it
ac2 install                             # install this workspace's services, with its tests
ac2 doctor                              # verify the configuration of everything
ac2 run people                          # terminal 1: the server
ac2 run helper                          # terminal 2: the agent
ac2 logs helper                         # terminal 3: read its log
ac2 errors                              # list what failed, with the stack traces
```

## The commands

| Command | What it does |
| --- | --- |
| `ac2 init NAME` | Create a workspace: places for agents and MCP servers, the shared registries and policy rules, and the checks CI runs. |
| `ac2 new agent NAME` | Add an agent: a LangGraph graph, a tool, an HTTP entry point and its tests. `--model fake`, `anthropic` or `bedrock`; `--mcp SERVER` to let it call a server. |
| `ac2 new mcp NAME` | Add an MCP server over sample data, or over your own CSV files (`--from-csv`), an OpenAPI document (`--from-openapi`) or a Redshift schema (`--from-redshift`). |
| `ac2 link AGENT SERVER` | Let an agent call the tools of a server: registry entry, policy rules and a test. |
| `ac2 install` | Install this workspace's agents and MCP servers and the development tools with the Python you run this with. It never installs the SDK, which has its own installer; it stops with the fix if the SDK is missing. |
| `ac2 doctor [SERVICE]` | Verify the configuration: settings, adapters, registries, rules, pins. Exit code 1 if a check fails, with the fix. Also `ac2 config check`. |
| `ac2 config explain SERVICE` | Every setting of a service and where it came from. Secrets are masked. |
| `ac2 config options [PORT [NAME]]` | The adapters of a section, or the options of one adapter with their defaults. |
| `ac2 config schema` | A JSON Schema of `agentcraft2.settings.toml`, for an editor or a coding assistant. |
| `ac2 check` | What CI runs: lint, format, types, tests and the policy samples. Stops at once, with the fix, if the services are not installed. |
| `ac2 data model draft DATA_DIR` | Draft a `data_model.csv` from a folder of CSV files: columns, types, keys and joins, with the rest marked TODO. See [the data model](data-model.md). |
| `ac2 data model diff DATA_DIR` | List where the CSV files differ from an existing `data_model.csv`. Exit code 1 if there is a difference. |
| `ac2 data model check DATA_DIR` | Check a `data_model.csv` against the standard and the files. Exit code 1 if there is an error. |
| `ac2 eval [SERVICE]` | Run the evals of a service with a real model. |
| `ac2 policy test` | Try the policy samples against the rules, and with `--opa` against OPA. |
| `ac2 graph AGENT` | The graph of an agent as a Mermaid diagram. |
| `ac2 run SERVICE` | Start a service and keep it running. What it writes is shown as it is written and kept in its log. |
| `ac2 services` | List Phoenix and Agent Lab with their state and address; also `ac2 services list`. |
| `ac2 services start [NAME]` | Start a service the SDK installed, in the background, and wait until it answers. |
| `ac2 services stop [NAME]` | Ask a service to stop, as Ctrl+C does, and wait until it has. |
| `ac2 logs [SERVICE]` | Read the log of a service, of Phoenix or Agent Lab, or of all of them. |
| `ac2 errors [SERVICE]` | List the failures: the exact message, what was expected and found, the fix, and the stack trace. |
| `ac2 registry pin SERVER` | Pin the input schema of every tool of a server in the tool registry, so a changed schema is noticed. |
| `ac2 update` | Bring generated files up to what this version writes; `--plan` previews. |
| `ac2 deploy SERVICE` | Write the Terraform and Dockerfiles that run a service on AWS. It applies nothing. |

## When something fails

The command line is meant to fail fast and tell you everything it knows, on your machine.

- **A mistake in what you typed** (a service that is not there, a missing file) is a few
  lines: what went wrong, what was expected, what was found, how to fix it. Add the global
  option `--trace` (`ac2 --trace run helper`) to get its stack trace as well.
- **A failure from deeper down** (a configuration error, a service that cannot start, a
  model or a server that cannot be reached) shows the full stack trace first, then the
  type and the exact message, then what was expected and found, the causes, where it was
  raised and the line of your own code nearest to it.
- **A fault in the command itself** shows the full stack trace and says it is a fault in the
  command, not in your workspace.
- **Ctrl+C** stops quietly with exit code 130.

On a server the same errors are logged by their type and where they were raised, not their
text, so a secret or a caller's content never reaches a log.

## Logs and errors

`ac2 run` keeps what a service writes, one file per service, in the state folder:
`<service folder>/.agentcraft2/logs/<service>.log`, or `.agentcraft2/logs/` under
`AC2_HOME` when that is set. It is JSON lines: the service's own log lines as they are, and
anything else it prints, a crash above all, gathered into one entry. A file over 5 MB is set
aside as `<service>.log.1` when the service starts again.

```bash
ac2 logs                          # the latest lines of every service
ac2 logs helper -n 100            # the last 100 of one
ac2 logs helper --level ERROR     # only what failed
ac2 logs --request <request-id>   # everything one request did, across services
ac2 logs helper -f                # keep showing new lines (Ctrl+C stops)
ac2 logs helper --raw             # the JSON lines, for jq and scripts
ac2 errors                        # the latest failures of every service, with stacks
ac2 errors helper -n 3 --no-trace # the last three, as messages only
```

A service started another way, such as `python -m`, writes only to its own terminal, and
there is nothing for `logs` and `errors` to read.

## Services

Phoenix, the viewer for the traces of your agents, and Agent Lab, where you chat with them in a
browser, are the services of the SDK. The SDK installs them once, with itself, and `ac2` only
starts and stops them: there is no `ac2` command that installs a service. That keeps the SDK's
tools apart from your agents, and from the packages `ac2 install` puts in your Python. Agent Lab
needs libraries that conflict with your agents' (its chat library wants MCP 1.x, they use MCP
2.x), which is why it has a folder of packages of its own.

**Where things are.** The code of a service is in the SDK's own folder, `AC2_SDK_PATH`: by
default `agentcraft2-sdk` in `%LOCALAPPDATA%` on Windows, which belongs to the machine and is not
roamed, and `~/.agentcraft2_sdk` elsewhere. The installer refuses a folder on a network drive or
UNC path, inside OneDrive or in the roaming profile. What a service writes is in your workspace's
state folder, `.agentcraft2/` (or under `AC2_HOME` when that is set), which Git ignores:

| Where | Holds |
| --- | --- |
| SDK folder `services/<name>/lib/` | the packages of the service, in a folder of their own |
| SDK folder `services/<name>/installed.json` | which version of the SDK installed it |
| workspace `.agentcraft2/services/<name>/data/` | its data: Phoenix's database of traces; Agent Lab's configuration, sign-in code and conversations |
| workspace `.agentcraft2/services/<name>/run/` | what `ac2 services` and the service's supervisor say to each other |
| workspace `.agentcraft2/logs/<name>.log` | its log, beside the logs of your agents |

```bash
python tools/install_sdk.py     # once, in your checkout of the SDK: installs the SDK, Phoenix and Agent Lab
ac2 services                    # what is installed and running, and where it listens
ac2 services start              # start, wait until it answers
ac2 services start lab          # one service
ac2 services stop               # ask for a stop, wait for it
ac2 logs lab                    # read its log; ac2 errors lists what failed
```

**Agent Lab.** `ac2 services start lab` writes Agent Lab's configuration from your workspace
every time: the agents in `agents/`, the address each has in `registry/agents.yaml`
(`ac2 new agent` writes it), and the settings of the first agent (all must run as the same
developer). Conversations and the sign-in code are kept in
`.agentcraft2/services/lab/data/`. It prints where the sign-in code is; open
`http://127.0.0.1:8765` (not `localhost`) and enter it. Agent Lab only talks to agents that are
running, so start each one, with its MCP servers, with `ac2 run`: it does not start them.

Without a checkout, the installer is `python -m agentcraft2_cli.sdk install --version
SPECIFIER`, and `python -m agentcraft2_cli.sdk check` says whether the tools are usable.

`start` checks before it starts anything: the service must be installed by this version of the
SDK (otherwise it says to run the installer again), its folders must be on a local disk, and
its port must be free. Phoenix is one program on one port, `127.0.0.1:6006`, where an agent
with `AC2_PHOENIX_ENABLED=true` sends its traces; Agent Lab is one program on `127.0.0.1:8765`.
If another workspace started one, `ac2 services` shows `running elsewhere`, `start` says where,
and `stop` stops it from any workspace. On Windows the path to the packages has to leave room
for the longest file of a service (150 characters for Phoenix); the installer checks this before
it changes anything.

## Settings

`ac2 config explain <service>` says which variable, `.env` file or settings file supplied each
setting. See [the variables](variables.md), `agentcraft2.settings.toml` in
[getting started](getting-started.md#your-choices-in-a-settings-file), and `AC2_HOME` for
keeping a project's files and state in one folder.
