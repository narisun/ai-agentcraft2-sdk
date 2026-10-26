# agentcraft2-sdk

Build Python agents and shared tools with consistent configuration, permissions,
audit records, and tests. You write the application logic; agentcraft2-sdk connects
it to models, data sources, and the services around each request.

An **agent** uses a language model to answer questions and choose tools. A
**tool** is a function it can call. LangGraph runs the agent's steps; MCP
(Model Context Protocol) lets a separate server expose tools to several agents.

Choose the path that matches your task:

| I want to… | Start here | What I will have |
| --- | --- | --- |
| Test an existing agent in a browser | [Run Agent Lab](#run-agent-lab) | rmprep, its MCP server, chat and memo versions |
| Build my first agent | [Run your first agent](#run-your-first-agent), then the [tutorial](docs/getting-started.md) | A generated Python workspace with runnable code and tests |
| Understand the design | [Developer guide: principles](docs/developer-guide.html#principles) | How LangGraph, interfaces, dependency injection, and environment providers fit together |
| Change the library or simulator | [Working on the library](#working-on-the-library) and the [code map](docs/README.md#find-the-code) | The right package, environment, and checks for a change |

The [documentation index](docs/README.md) separates setup instructions, reference
material, and design proposals. The current checkout includes Agent Lab as a local
development preview; use the [release scope](docs/release-scope.md) for support boundaries.

## Run Agent Lab

Agent Lab is the browser simulator for testing agents. It runs in a Python environment of
its own, because its chat library (Chainlit) needs MCP 1.x and the agents need MCP 2.x.
The one-line launchers that used to set this up (`setup-local`, `start-local`,
`stop-local` and `start-phoenix`) have been removed, and `ac2 services` is
the planned replacement for starting and stopping Agent Lab and Phoenix. Until it
is released, follow the [Agent Lab package guide](packages/agentcraft2-ui/README.md): it
installs Agent Lab, starts your agent and its MCP servers, and connects them.
[Local testing](docs/local-startup.md) has the model settings and the rmprep walkthrough
with its expected answers.

Phoenix, the local trace viewer, has no start command in this checkout until
`ac2 services` is released. Agents run without it, and tracing stays off unless you
turn it on.

## Run your first agent

You need Python 3.11 or newer with pip, a copy of this repository, and access
to your approved package index or a folder of pre-downloaded Python wheels.
The tutorial uses a fake model that runs in the Python process and local
providers for configuration, permissions, audit records, and conversation state.
Local is the default for every setting, so no cloud infrastructure is needed;
AWS is a per-variable opt-in described in the
[configuration reference](docs/variables.md).

Open a terminal in the repository's top-level `ai-agentcraft2-sdk/` folder. Choose
the commands for your terminal below. They use your existing Python and create
an application workspace named `tutorial/` beside the library checkout. Use a different workspace name if that folder already exists.

Packages install into the selected Python's configured location through its pip. If you already created
this tutorial using the developer guide,
continue with [the next steps](docs/getting-started.md#2-understand-your-project).

### Windows Command Prompt (cmd.exe)

In **Command Prompt**, run the commands one line at a time from the library
root. `set "PYTHON=..."` stores the Python command or executable path; `"%PYTHON%"` runs
it. Keep the quotes so paths containing spaces work.

```bat
set "LIBRARY=%CD%"
set "PYTHON=python"
"%PYTHON%" -m pip install -e ./packages/agentcraft2-core -e ./packages/agentcraft2-cli
"%PYTHON%" -m agentcraft2_cli init tutorial --dir .. --lib-path "%LIBRARY%" --owner learning
cd /d ..\tutorial
"%PYTHON%" -m agentcraft2_cli new agent helper --description "A practice assistant" --model fake
"%PYTHON%" -m agentcraft2_cli install
"%PYTHON%" -m pytest
"%PYTHON%" -m helper "Say hello to Ada."
```

If your team supplies a specific executable, set `PYTHON` to its full path.
Use `python -m pip`, so pip runs under that approved Python.

### macOS or Linux, using bash or zsh

```bash
LIBRARY="$PWD"
PYTHON=python3
"$PYTHON" -m pip install -e ./packages/agentcraft2-core -e ./packages/agentcraft2-cli
"$PYTHON" -m agentcraft2_cli init tutorial --dir .. --lib-path "$LIBRARY" --owner learning
cd ../tutorial
"$PYTHON" -m agentcraft2_cli new agent helper --description "A practice assistant" --model fake
"$PYTHON" -m agentcraft2_cli install
"$PYTHON" -m pytest
"$PYTHON" -m helper "Say hello to Ada."
```

**Expected result:** the tests pass and the agent prints:

```text
fake: Say hello to Ada.
```

The fake model echoes your question. The tutorial uses scripted model replies
to test tool calls, then shows how to configure a real model.

`--lib-path` connects the workspace to this library checkout. `install` uses
the current Python's pip to install the workspace services and development
tools. Run it again after adding a service or changing its dependencies.

Continue with [adding a tool and testing it](docs/getting-started.md#3-add-a-tool-test-first).
Keep the terminal open: the tutorial reuses the Python variable and workspace.

## Where to make a change

Your generated application lives in `tutorial/`. The library lives in
`ai-agentcraft2-sdk/`. They have different responsibilities:

| You want to… | Start here |
| --- | --- |
| Change what a tool does | `tutorial/agents/helper/src/helper/tools.py` |
| Change the agent's steps or prompt | `tutorial/agents/helper/src/helper/graph.py` |
| Write a behavior test | `tutorial/agents/helper/tests/` |
| Allow a specific action | `tutorial/policies/agentcraft2/rules/data.yaml`, with a sample in `tutorial/tests/policy-samples.yaml` |
| Choose a model or adapter | `tutorial/agents/helper/.env` and the service's `pyproject.toml` dependencies |
| Share tools through MCP | [Add an MCP server](docs/getting-started.md#5-add-a-shared-tool-server) |
| Change shared library behavior | The packages in this repository; see [contributing](#working-on-the-library) below |

An **adapter** implements an interface for a service, such as a model or a
data source. `ServiceContainer` builds the configured adapters and supplies
them to application code. This is dependency injection: the graph depends on
interfaces, so tests can supply fakes and deployments can select other providers.

## Run and check the application

From `tutorial/`, in the same Command Prompt window:

```bat
"%PYTHON%" -m agentcraft2_cli doctor helper
"%PYTHON%" -m agentcraft2_cli check
"%PYTHON%" -m agentcraft2_cli run helper
```

On macOS/Linux, replace `"%PYTHON%"` with `"$PYTHON"`. In a new terminal,
[restore the interpreter variable](docs/getting-started.md#using-a-new-terminal)
before running commands.

- `doctor` checks the configured services. With cloud adapters, it can contact
  external services.
- `check` runs lint, formatting, type checks, tests, and policy samples.
- `run helper` starts an HTTP service. It keeps running until you press Ctrl+C.
  To ask one question from the command line instead, use
  `"%PYTHON%" -m helper "Say hello to Ada."`.

The [HTTP example](docs/getting-started.md#6-optional-call-the-agent-over-http)
uses Python's standard library to send a request. The service exposes
`/healthz` for liveness and `/readyz` for readiness. Readiness waits for
service validation and graph/MCP preparation. Generated agents support live
model drafts through `POST /invoke` with `Accept: text/event-stream`, followed
by one accepted result. The separate `/invoke/stream` route sends graph-step
updates for debugging. See [live responses and metrics](packages/agentcraft2-ui/README.md#live-responses-and-metrics).

## Inspect model input, output, and conversation history

To inspect an agent's model calls, add this to its `.env` and restart:

```dotenv
AC2_MODEL_TRACE_PATH=.agentcraft2/model-trace.jsonl
```

The file is written under that service's `.agentcraft2/` folder. It records the
messages sent to the provider, tool definitions, responses, and retry attempts.
Capture is opt-in and retains conversation content; use test data and keep the
file private. Recognizable credentials are masked.

The [debugging Q&A](docs/developer-guide.html#faq) explains how to read the trace,
continue a conversation, and inspect checkpoint state. Its
[runnable ReAct example](docs/developer-guide.html#react-context) follows a tool
call and a second question through the generated graph using scripted replies.

Every new workspace also includes the [verified Anthropic walkthrough](docs/verified-anthropic-run.md),
selected-field trace samples, and a JSONL reader. The walkthrough follows a real
run call by call and shows how a tool result and the saved first turn reach the
model on the next call.

From your generated workspace, run the scripted example:

```bat
"%PYTHON%" -m helper.examples.trace_react
"%PYTHON%" examples/read_trace.py agents/helper/.agentcraft2/react-demo.jsonl --last 6
```

For a live run, follow [Anthropic setup](docs/anthropic-example.md) to add its
dependency extra and configure your model ID and API key. Then run:

```bat
"%PYTHON%" -m helper.examples.trace_anthropic
```

It sends two questions on the same thread, prints the model trace and checkpoint
snapshots, and accepts `--thread` to continue a saved conversation. Other agents
have these examples under their own package names; their READMEs give exact commands.

## Examples

`examples/rmprep` and `examples/rmqna` are static workspaces that `agentcraft2` created and
that a team then built on. They are not part of this repository's build: nothing in `packages`
imports them, and the lint, type and test runs here skip them. Copy a folder anywhere, or
download the `example-rmprep` or `example-rmqna` zip from the latest run of the
[Examples workflow](.github/workflows/examples.yml), then:

```bash
python -m pip install "agentcraft2-cli>=0.1,<0.2"
cd rmprep
ac2 install
ac2 check
```

The Examples workflow does exactly this on a copy of each folder, against the library in this
checkout, once a week and whenever an example or a package changes, so an example that stops
working shows up there.

## Explore the repository

| Path | What it contains |
| --- | --- |
| [Core package](packages/agentcraft2-core/README.md) | Contracts, configuration, service composition, local adapters, model/tool checks, framework integrations, and test helpers |
| [AWS package](packages/agentcraft2-aws/README.md) | AWS adapters and their optional dependencies |
| [CLI package](packages/agentcraft2-cli/README.md) | The `agentcraft2` command, also available as `python -m agentcraft2_cli` |
| [Example: relationship manager preparation](examples/rmprep/README.md) | A static workspace: router, memo and Q&A nodes over 33 supplied CSVs, with governed MCP evidence, source references and checkpoints |
| [Example: relationship manager questions](examples/rmqna/README.md) | A static workspace: one agent that writes read-only SQL, and the MCP server that validates and runs it |
| [Agent Lab package](packages/agentcraft2-ui/README.md) | `packages/agentcraft2-ui`: the browser simulator with chat, memo versions, trace links, answer feedback, and online-eval export of saved chats |
| [Policies](policies/README.md) | Rules and the OPA policy bundle |
| [Architecture tests](tests/architecture) | Checks that enforce package boundaries and keep documentation current |
| [Configuration reference](docs/variables.md) | Supported environment variables |
| [Identity guide](docs/identity.md) | User identity and delegated service calls |
| [Evals guide](docs/evals.md) | Offline evals with baselines (`agentcraft2 eval`) and online scoring of sampled, saved chats |

The repository root holds shared development settings. Install the packages
under `packages/` using pip or the development helper below.

## Working on the library

Use this workflow when changing the library itself. Open a terminal at the
library root. If you followed the quickstart, return there with
`cd /d "%LIBRARY%"` in Command Prompt or `cd "$LIBRARY"` in bash/zsh.

Select your approved Python, then build and install the SDK with the contributor tools:

```bat
rem Windows Command Prompt, from ai-agentcraft2-sdk/
set "PYTHON=python"
install-sdk.bat --dev --editable
```

```bash
# macOS/Linux, from ai-agentcraft2-sdk/
PYTHON=python3
./install-sdk.sh --dev --editable
```

`install-sdk.bat` and `install-sdk.sh` run `tools/install_sdk.py` with the Python in `PYTHON`
(`python` or `python3` on your PATH when it is not set) and pass every option on; they stop
with the fix when that Python cannot run. `tools/install_sdk.py` is the one script that builds and installs the SDK. It checks the
environment, looks for an SDK that is already installed, builds the packages, uninstalls the
old SDK and only then installs the new one, printing each step as it goes and stopping at the
first problem. pip prints only problems, so the steps stay readable; add `--verbose` to see
everything pip prints. It also warns about a `~gentcraft2-...` folder that an interrupted
uninstall left behind, and says how to delete it. With `--dev --editable` it installs the local packages, optional dependencies
and development tools. Without options it builds wheels and installs them, as a consumer
would get them (`--no-index --find-links=...` and other pip options are passed to pip). When it finishes it
says whether the `ac2` command is on your PATH; if it is not (a base Python's scripts folder often is not), it
names the folder and the `python -m agentcraft2_cli` form that works without it. Then run these checks from
the library root, replacing `"%PYTHON%"` with `"$PYTHON"` on macOS/Linux:

```bat
"%PYTHON%" -m ruff check .
"%PYTHON%" -m ruff format --check .
"%PYTHON%" -m mypy
"%PYTHON%" -c "from importlinter.cli import lint_imports; lint_imports()"
"%PYTHON%" -m pytest
"%PYTHON%" docs/guide/build_guide.py --check
```

Ruff checks code style and formatting, mypy checks types, and import-linter
checks package dependency boundaries. Pytest runs the tests; the final command
checks that the generated developer guide matches its source.

Follow the [code and documentation conventions](docs/developer-guide.html#code-conventions)
when contributing. They explain where code belongs, how to name components, and
how to document ownership, failures, and examples.

The default tests block connections to external hosts while allowing
loopback for local tests and Python's event loop. Tests that need OPA,
Terraform, Docker, a database, or cloud credentials are separate, opt-in
checks for suitable machines. CI also gates coverage: the core `contracts`,
`config`, `di`, and `pipeline` packages must keep 90% branch coverage. See
[CI](.github/workflows/ci.yml) and the
[release scope](docs/release-scope.md) for those checks and supported configurations.

When editing the developer guide, change `docs/guide/guide.src.html`, then
regenerate the HTML and run its tests:

```bat
"%PYTHON%" docs/guide/build_guide.py
"%PYTHON%" -m pytest tests/architecture/test_generated_docs.py
```

Keep the generated HTML and companion Markdown/sample files with the source change.
The walkthroughs and trace reader come from the CLI templates, so the library
docs and newly generated tutorials stay in sync. Configuration reference
tables come from the binding definitions in
`packages/agentcraft2-core/src/agentcraft2_core/config/bindings.py`.

## Deployment and release scope

The [deployment walkthrough](docs/getting-started.md#10-optional-prepare-a-deployment)
uses the Python CLI to generate AWS Fargate deployment files for review.
To build images and apply infrastructure, use a build machine or CI runner
with a container engine, Terraform, and the required registry and AWS permissions.

The [specification](docs/Specification.md) describes the target design.
The [release scope](docs/release-scope.md) records supported behavior and
operational limits. The [architecture decisions](docs/adr/README.md) explain
the design choices.

For Agent Lab's current setup and features, use its
[package guide](packages/agentcraft2-ui/README.md). The
[documentation index](docs/README.md#current-behavior-and-design-material) links
the frontend design, implementation plan, and original Chainlit trial separately.
