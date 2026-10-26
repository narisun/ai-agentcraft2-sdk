# Trace agents with Phoenix

Phoenix gives Agent Lab a timeline of each agent response: model messages, MCP
calls, token usage, duration, and links back to checkpoint IDs. It is the only
agent-tracing backend the library ships, and it stays off until you enable it.

In a workspace made with `ac2`, the SDK's installer installs Phoenix with the SDK and
`ac2 services start phoenix` starts it; see [the `ac2` reference](cli.md#services).

Phoenix runs on your own machine, with traces in one SQLite file. It needs no account
and no key, and nothing leaves the machine: [traces stay on your machine](#traces-stay-on-your-machine).

**Starting Phoenix.** The `start-phoenix` and `start-local` launchers that used to start
it have been removed. The SDK's installer installs Phoenix, and `ac2 services start phoenix`
starts it. This page explains what to turn on in the agent, the settings, trace inspection,
privacy, and the captured verification results.

For the first-run path with Agent Lab, use [local startup and testing](local-startup.md).
Commands are shown for Command Prompt. On macOS/Linux, open a bash or zsh terminal
at the repository root, use `python3` for `python`, and use `/` for `\`.

## Generated services: one setting

A service made with `agentcraft2 new agent`, and any service that starts through
`run_agent`, has no tracing code to write. `run_agent` resolves the Phoenix
settings, starts the exporter, hands the same adapter to the service container
and to the HTTP app, and closes it after the container so spans flush last. Turn
tracing on in the service's `.env`:

```dotenv
AC2_PHOENIX_ENABLED=true
```

That is the whole integration. With nothing else set, the agent exports to
`http://127.0.0.1:6006` under a project named for the service, so a Phoenix server
must be listening there (see [Starting Phoenix](#1-start-phoenix)). **Content capture
stays off**: traces carry metadata, token counts, and checkpoint IDs, not
conversation text. Set `PHOENIX_PROJECT_NAME` to group several agents' traces, and
`AC2_PHOENIX_CAPTURE_CONTENT=true` only with approved test data (see [Privacy and
content capture](#privacy-and-content-capture)). Restart after changing a
setting. Tracing covers `POST /invoke`, including Agent Lab's live response
mode; the standalone CLI and the `/invoke/stream` graph-step endpoint keep their
existing behavior.

A generated agent's own `agents/<name>/docs/tracing.md` gives the same quick
start with its name filled in. A service that composes by hand follows
[Compose tracing by hand](#compose-tracing-by-hand) instead.

The rest of this page covers the local server (steps 1 to 4), lists every
setting, and covers where traces go, privacy, hand composition, and
troubleshooting.

## 1. Start Phoenix

Phoenix is the `arize-phoenix==20.21.1` package. Your package administrators must make it
and compatible wheels for your platform available through the enterprise package
repository. The SDK's installer installs it with the SDK (in the SDK's own folder, apart from
your workspaces), and `ac2 services` starts and stops it:

```bat
ac2 services
ac2 services start phoenix
ac2 services stop phoenix
```

`ac2 services` lists each service with its state. Phoenix serves only this machine, on
`http://127.0.0.1:6006`; its traces, logs and run state go in the workspace's `.agentcraft2` folder.

A Phoenix server that listens on `http://127.0.0.1:6006` (or `localhost`, or `[::1]`, with
an optional port) receives spans at `http://127.0.0.1:6006/v1/traces`. The agent accepts only
an address on this machine. It is a trace viewer for your own machine: keep its HTTP
listener on loopback, and turn off its Playground, its own MCP endpoint, code execution and
built-in assistant, since Agent Lab and the agent's registered MCP servers cover those.

## 2. Enable Phoenix in the agent's .env

Add these settings to the agent's own `.env` (for rmprep,
`examples\rmprep\agents\rmprep\.env`). Keep your existing model provider and API key
in that file.

```dotenv
AC2_TELEMETRY=off
AC2_PHOENIX_ENABLED=true
AC2_PHOENIX_CAPTURE_CONTENT=true
PHOENIX_COLLECTOR_ENDPOINT=http://127.0.0.1:6006
PHOENIX_PROJECT_NAME=rmprep-trial
```

For this local setup, `AC2_TELEMETRY=off` disables the separate operational OTLP
trace/metric exporter. Phoenix still receives agent/model/tool spans and token
metrics. Enable operational telemetry only when you have configured its own
collector; Phoenix is not the general OTLP metrics endpoint. If your
`AC2_AUDIT_OPTIONS` still sets the older `"tracing": true`, remove it or set it
to `false` as well.

`AC2_PHOENIX_CAPTURE_CONTENT=true` records model messages and tool
arguments/results, so use approved test data; its default is `false`. The agent's
existing JSONL traces, audit records, and checkpoint database continue to work
independently of Phoenix.

## 3. Start the agent and Agent Lab

With a Phoenix server running, start the MCP server, the agent and Agent Lab as the
[local startup guide](local-startup.md#3-start-the-services-and-open-agent-lab) shows. Restart
the agent after changing a setting. Open Agent Lab at <http://127.0.0.1:8765> and Phoenix at
<http://127.0.0.1:6006>. This local, single-user Phoenix setup needs no separate login.
SQLite keeps the traces in the Phoenix server's data folder, so restarting keeps them.

## 4. Verify a conversation

Choose **rmprep** and start a new chat. These questions exercise a real MCP call
without requesting client financial or contact records:

Send each question in the same chat after the previous answer finishes:

1. `Find clients containing the exact synthetic test name DISTINCT-FIX-NO-MATCH-20261016. Use find_clients once and report the number of matches. Do not broaden the search or retrieve other records.`
2. `Using the result already in this conversation, what exact name did I ask you to search for and how many matches did you find? Answer in one sentence without calling tools.`
3. `Without searching again, restate the exact search name and match count from our earlier exchange, and explain whether that result identifies a client for a memo. Keep it to two sentences.`

Accepted answers and confirmed agent failures include **Open trace in Agent Lab**
below usage and response time when execution created a trace. The link opens the
configured Phoenix trace. Export is asynchronous; allow a few seconds for ingestion.
Opening a trace does not rerun the agent.

In Phoenix, inspect:

| Item | What to look for |
| --- | --- |
| Root `rmprep.turn` span | One trace per invocation; the three traces share `session.id` and `agentcraft2.thread_id`. |
| `model` spans | Select a model span, then view **Input** and **Output** on its **Info** tab. Switch between **Messages** and **Raw**. The next model call includes the preceding tool result. |
| `rmprep-data.find_clients` span | MCP arguments, result, duration, and `agentcraft2.mcp_server`. The synthetic search should return zero matches. |
| Usage | Prompt/completion tokens and cache read/write counts. Prompt totals already include cache tokens. |
| Checkpoints | `agentcraft2.checkpoints` on the root span contains checkpoint IDs and parent IDs for that request. Use Agent Lab's **Logs** page to inspect saved state; chat links directly to Phoenix. Failed runs include available checkpoints and an error status; their rejected drafts remain in model traces, not accepted chat history. |
| Follow-up | Its model input includes the earlier exchange, and its answer reports the same search and count. |

Model spans measure provider-call duration; the root measures agent work including
tools. Agent Lab measures the full response time, including HTTP/UI overhead, so
the durations need not be identical. Checkpoint contents remain in the agent's
database; Phoenix receives references, not a second copy of the checkpoint store.

<details>
<summary>Captured runs: October 15 and 16, 2026</summary>

These are dated records of real runs through Agent Lab, kept so you can compare
your own numbers. Model output, caching, and timing vary between runs. They were made with
the `start-local` and `stop-local` launchers, which have since been removed.

### Verified local run: October 16, 2026

The three questions above ran through Agent Lab using the root `.env`, default
Python 3.13.0, Anthropic `claude-sonnet-4-5`, Phoenix 20.22.0 and SQLAlchemy 2.1.4.
Startup masked credentials, and every observed Python process used the default
installation.

| Turn | Model calls | MCP calls | Input / output tokens | UI response time | Checkpoint references |
| --- | --- | --- | --- | --- | --- |
| Search for the synthetic name | 2 | 1 | 8,572 / 127 | 5.22 s | 4 |
| Recall the name and count | 1 | 0 | 4,472 / 37 | 2.12 s | 4 |
| Explain whether the result identifies a client | 1 | 0 | 4,548 / 60 | 2.07 s | 4 |

The search returned zero matches. Both follow-ups recalled the exact name and
count on the same thread; the last answer correctly said no client had been
identified for a memo. Model inputs included the earlier question, tool result
and saved answers. The MCP log showed the query, parameters and zero-row result.
Agent Lab's token counts matched Phoenix, and all twelve checkpoint references
resolved to saved checkpoints.

Phoenix trace browsing showed model input/output and usage. The service logs
contained no SQLAlchemy warning or application error through startup, requests,
browsing and shutdown. All four services stopped gracefully;
ports 6006, 8000, 8100 and 8765 closed. The root `.env` and existing saved
conversations, traces and checkpoints were preserved. These are observed results;
model wording, token counts and timings can vary.

That run used Phoenix 20.22.0 with a launcher-side SQLAlchemy query fix, which this
repository no longer carries. The pinned 20.21.1 leaves the warning visible (see
[Remaining upstream warning](#remaining-upstream-warning)), so the note above that the
logs held no SQLAlchemy warning describes that run only.

### Verified Anthropic run

On October 15, 2026, an earlier two-question test ran through Agent Lab using the root
`.env` and `claude-sonnet-4-5`. The synthetic name returned zero matches. The
follow-up recalled that name and count without another MCP call.

| Turn | Model calls | MCP calls | Input / output tokens | Cache read / write tokens | UI response time | Checkpoint references |
| --- | --- | --- | --- | --- | --- | --- |
| Find the synthetic name | 2 | 1 | 8,135 / 115 | 3,970 / 4,157 | 5.02 s | 4 |
| Recall the result on the same thread | 1 | 0 | 4,235 / 33 | 4,157 / 75 | 1.89 s | 4 |

The first trace showed `rmprep-data.find_clients` returning an empty `matches`
list. The next model input included that tool result with the same tool-call ID.
The follow-up's input included the original question, tool result, saved answer,
and new question. The traces shared one session and thread, with separate request IDs.

Phoenix's API and SQLite records matched Agent Lab's saved metrics, the agent's
model JSONL records, the MCP server audit, and all eight checkpoint references.
Both traces remained available after stopping and restarting Phoenix with the
same SQLite file.
These counts are an example of one run; model output, caching, and duration vary.

### Verified JONE-0022 memo and follow-ups

On October 15, 2026 (America/New_York), the original broad memo request for
JONE-0022 as of 2026-08-03 completed through Agent Lab using Anthropic and Phoenix.
The memo had 867 words, including references, and required no revision. Two
follow-ups used the same thread:

1. Explain the $7M net working-capital decline and 19-day cash-conversion-cycle increase from saved evidence.
2. Ask whether the sweep was deployed in October 2025, retrieving opportunity and service-request records through MCP.

| Turn | Model calls | MCP calls | Input / output tokens | Cache hit | UI time | Checkpoint references |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 2 | 1 | 29,550 / 2,921 | 27.3% | 68.58 s | 4 |
| 2 | 1 | 0 | 28,572 / 532 | 0.0% | 11.09 s | 4 |
| 3 | 2 | 2 | 59,727 / 598 | 96.7% | 13.17 s | 4 |

The calculation answer reused saved evidence. The October question retained the
August memo and retrieved records through its 2026-08-03 cutoff. Both the memo
and follow-up described Closed Won as a sales stage and left implementation
unverified. Each answer linked to Phoenix; model inputs/outputs, MCP audit events,
usage totals and all 12 checkpoint references matched the local records. Source
checks covered 59 CSV records, 579 fields, nine payment groups and 14 comparisons.

The memo and Q&A specialists have different static prompt prefixes. The first
Q&A call wrote its cache; the next follow-up reused it. Cache hits and timing vary
between runs. Regression tests also cover trace links and checkpoint references
for confirmed failed turns; the live conversation above had no failed turns.

### Verified Jones follow-up after restart

On October 16, 2026, Agent Lab reopened an existing conversation containing Canada
Goose and Jones Dairy Farm memos. A follow-up named the saved Jones memo and asked
about covenant breaches, the cash conversion cycle change and wallet-share calculation.

| Model calls | New MCP calls | Revisions | Input / output tokens | UI time | Checkpoint references |
| --- | --- | --- | --- | --- | --- |
| 1 | 0 | 0 | 12,391 / 657 | 6.69 s | 4 |

The answer reused the selected memo's saved evidence. It reported DSCR 1.08,
fixed charge coverage 0.96, cash conversion cycle 87 → 106 days, and recorded
wallet share 40.21%, preserving the unknown waiver execution and wallet-share
methodology. Phoenix input/output and usage matched Agent Lab and the model JSONL.
Source checks covered eight CSV records, 91 fields and seven calculated comparisons.
Both memo versions remained saved; all four services stopped
without changing stored conversation, checkpoint or trace rows during shutdown.

The earlier follow-up needed two calls because Markdown markers pushed the raw
word count over its limit. Content-word counting now excludes those markers;
regression tests preserve that original draft unchanged and still reject invalid
citations and answers over their content-word limit. Live wording, timing and call counts vary.
Follow the [local test questions](local-startup.md#generate-a-memo-and-ask-follow-ups)
to run this scenario with your own saved memo reference.

</details>

## Settings

From your agent workspace, run `python -m agentcraft2_cli config explain rmprep`
to see effective Phoenix settings and where each came from. Run `python -m
agentcraft2_cli doctor rmprep` to validate them before startup. These tracing checks
do not contact Phoenix. Startup, `doctor` and `config explain`
all resolve the settings through the same function,
`resolve_phoenix_settings`, so a check shows what startup will use.

| Variable | Purpose | Default |
| --- | --- | --- |
| `AC2_PHOENIX_ENABLED` | Enable tracing; a Phoenix server must be listening at the endpoint | `false` |
| `AC2_PHOENIX_CAPTURE_CONTENT` | Include model and tool input/output | `false` |
| `PHOENIX_COLLECTOR_ENDPOINT` | `http` origin on this machine (`localhost`, `127.0.0.1` or `[::1]`), without `/v1/traces`; set it only to use another local port | `http://127.0.0.1:6006` |
| `PHOENIX_PROJECT_NAME` | Project for this agent's spans: 1 to 128 letters, digits, `_` or `-` | The service's name |

Agent settings resolve in this order: process environment, then service `.env`. The
Phoenix server uses its own fixed local settings and does not load agent credentials.

These variables are read by Phoenix's own loader, not by the configuration
binding table. The validator accepts the two settings that start with `AC2_`
(and, for one minor release, their old `EAP_` spellings, with a warning);
the `PHOENIX_*` names are the vendor's own and are not checked for typos. [ADR
0004](adr/0004-tracing-configuration.md) records registering all four in the
binding table as future work. `PHOENIX_API_KEY` and `AC2_PHOENIX_ENV_FILE` are not
settings any more: see [traces stay on your machine](#traces-stay-on-your-machine).

## Traces stay on your machine

Phoenix is a tool for the machine you develop on. Nothing in the library can send a
trace to another host, and nothing carries a credential for one:

- **No key.** The local Phoenix takes no login and no key, so the agent sends none.
  `PHOENIX_API_KEY` is not read. A leftover value is ignored, and `doctor`, `config
  explain` and startup say so, without showing the value.
- **Local address only.** `PHOENIX_COLLECTOR_ENDPOINT` is for choosing another port on
  this machine. It must be `http` on `localhost`, `127.0.0.1` or `[::1]`; any other
  address stops startup with a message that names the setting and not the address.
- **No separate settings file.** `AC2_PHOENIX_ENV_FILE` existed to keep a key in a
  private file. It is no longer read; a value left in a file earns the same message.
- **Not on a server.** A service whose deployment environment is `dev` or `prod`
  refuses to start with tracing on, and `doctor` fails the same check. A deployed
  service reports through its telemetry setting, with its own collector, not through
  Phoenix.
- **Agent Lab links.** Agent Lab shows an **Open trace in Agent Lab** link only for
  `http://localhost`, `http://127.0.0.1` or `http://[::1]`, and drops any other link.

In a container the agent cannot reach a Phoenix on the host, because the host is not
loopback to it. Run the agent and Phoenix on the same machine for tracing.

Export is asynchronous and best effort. A collector outage never retries a business
request or discards an accepted answer.

## Privacy and content capture

Content capture is the one setting here with a privacy cost, so it is off unless
you turn it on.

| `AC2_PHOENIX_CAPTURE_CONTENT` | What a trace holds |
| --- | --- |
| `false` (default) | Includes, for example, span names and timings, token counts, request and thread IDs, a scoped session hash, checkpoint references, model and provider names, tool and MCP server names, artifact IDs, and error types. |
| `true` | All of the above plus model messages, tool arguments and results, and the request and accepted answer. For rmprep that includes client evidence. |

Even with capture off, request and thread IDs and checkpoint references identify
conversations, so plan access and retention for the Phoenix server. The browser
receives only a link to the trace on your machine.

The rule for any file or server holding model traces, and for files derived from
real conversations such as feedback logs and exported interactions:

- **Treat them as content-bearing.** Credential masking is best effort, not a
  substitute for keeping the data private: a question or answer that contains a
  secret is retained verbatim.
- **Keep them private.** Local Phoenix keeps traces in
  `.agentcraft2\phoenix\data\phoenix.db`, and the optional model-trace JSONL
  (`AC2_MODEL_TRACE_PATH`) usually sits in a service's `.agentcraft2` folder; Git
  ignores `.agentcraft2`. Never commit them, and restrict access to them the way
  you would restrict access to the Phoenix server.
- **Use approved test data locally.** Enable capture only for data you are
  cleared to inspect, and turn it off again when you finish.

[Measure an agent with evals](evals.md#6-privacy) applies the same rule to
feedback logs, exported interactions, and scored reports.

## Compose tracing by hand

A service built from the generated template, or started with `run_agent`, needs
none of this. Do it yourself only when a service composes its own entry point.

Use `agent_tracing` from `agentcraft2_core.integrations.langgraph.tracing`. Its
signature is `agent_tracing(application, config, *, dotenv_path=Path(".env"),
settings=None)`. It resolves the Phoenix settings from the process environment
and `dotenv_path` (pass `None` to read only the process environment, or pass
already-resolved `settings` to skip both), and yields a running adapter, or
`None` when tracing is off. Wrap the service lifetime so the container closes
before the exporter flushes, and pass the one adapter to both the container and
the response handler:

```python
from agentcraft2_core.integrations.langgraph.tracing import agent_tracing

async with agent_tracing(APPLICATION, config, dotenv_path=ENV_FILE) as tracing:
    services = ServiceContainer(config, model_trace=tracing)
    try:
        ...  # start and validate services, then build `graph`

        async def run(context, given):
            question = parse_question(given)
            if tracing is not None:
                return await tracing.run(
                    graph, services, context, question, respond=your_response_function
                )
            return await your_response_function(graph, services, context, question)

        ...  # serve, with `run` as the request handler
    finally:
        await services.aclose()
```

`your_response_function(graph, services, context, question)` keeps its current
behavior and returns the output dictionary. It must invoke the graph it is given,
because that graph carries the tracing callbacks. The adapter copies that
dictionary and adds `trace` metadata, so treat that key as reserved while tracing
is enabled. For isolated tests, construct
`PhoenixTracing(settings, application=..., exporter=...)` from
`agentcraft2_core.integrations.langgraph.phoenix` with an in-memory exporter
(OpenTelemetry's `InMemorySpanExporter`) instead of running a server. The rmprep service shows the full composition through `run_agent`.

The agent needs the library's `otel` extra, installed with
`python -m pip install "agentcraft2-core[otel]"`; generated agents already
declare it. Enabling tracing without it fails at startup with that command. The
agent does not import the Phoenix server package.

## Troubleshooting

### Remaining upstream warning

The pinned Phoenix can print SQLAlchemy's `Column-expression-level unary distinct()`
warning during startup or browsing. Its database queries use column-level
`distinct()` where SQLAlchemy 2.1 expects
[`select(...).distinct()`](https://docs.sqlalchemy.org/en/21/core/sqlelement.html#sqlalchemy.sql.expression.distinct).
We leave this warning visible and keep Phoenix's database code unchanged. It
does not block the verified SQLite trace ingestion and browsing workflow. Review
an upstream query fix with the next Phoenix upgrade; installing `aioboto3` will
not fix it.

Disabling Phoenix's unused MCP endpoint avoids the startup
`PydanticJsonSchemaWarning` caused by its unset-value defaults during schema
generation. Requesting Phoenix's OpenAPI schema separately can still expose
that upstream warning. It is not filtered. The startup suggestion to install
`aioboto3` no longer applies because Playground generation is disabled; Bedrock
agents keep their own model dependencies and can still send traces to Phoenix.

### Startup and trace checks

| Symptom | Check |
| --- | --- |
| Package installation fails | Ask your package administrators for `arize-phoenix==20.21.1` and compatible wheels for your Python version and platform. |
| Port 6006 is in use | Another Phoenix server (or program) already listens there. Stop it, or use it, or run yours on another local port and set `PHOENIX_COLLECTOR_ENDPOINT`. |
| Repeated connection errors on port 4318 | Set `AC2_TELEMETRY=off` for this local setup, or configure your separate operational collector. Restart the agent. |
| Startup says tracing needs `agentcraft2-core[otel]` | Install the extra with pip, as shown in [Compose tracing by hand](#compose-tracing-by-hand), then restart. |
| Startup rejects `PHOENIX_COLLECTOR_ENDPOINT` | Use `http://localhost`, `http://127.0.0.1` or `http://[::1]` with an optional port, and no path, query, or credentials. Traces are never sent to another host. |
| A note says `PHOENIX_API_KEY` is no longer used, or `AC2_PHOENIX_ENV_FILE` is no longer read | Delete the line. The local Phoenix needs no key, and settings come from the process environment and the service `.env`. |
| Startup says Phoenix tracing is a tool for a developer's machine | The service runs with a deployment environment of `dev` or `prod`. Set `AC2_PHOENIX_ENABLED=false`; a deployed service uses its telemetry setting. |
| Startup fails with `unknown configuration variable` naming a Langfuse setting | Langfuse support was removed. Delete those lines from the `.env`, or unset the variables in the process environment; see [Since the October review](release-scope.md#since-the-october-review). |
| No trace link | Check `AC2_PHOENIX_ENABLED` in the agent startup output and restart after changing settings. |
| Link opens before a trace appears | Allow a few seconds, then reload. Check the agent log for exporter connection errors. |
| Inputs and outputs are missing | Set `AC2_PHOENIX_CAPTURE_CONTENT=true` for approved test data, restart the agent, and send a new question. |
| The response succeeds while Phoenix is stopped | Expected: diagnostic export failures do not resend or fail an accepted agent response. Local inspection remains available. |

### Browse saved traces without starting the agent

Start the Phoenix server by itself to browse the SQLite traces. An externally managed endpoint
has its own lifecycle.

References: [Phoenix CLI](https://arize.com/docs/phoenix/self-hosting/deployment-options),
[SQLite storage](https://arize.com/docs/phoenix/self-hosting/deployment),
[OpenInference attributes](https://github.com/Arize-ai/openinference/blob/main/spec/semantic_conventions.md).
