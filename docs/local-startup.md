# Run and test rmprep locally

Use this guide to run **Agent Lab**, the **rmprep** agent and its **MCP data server** on a
Windows, macOS, or Linux desktop, and to check the answers. You need your team's
Python 3.11+ and pip setup, an approved package index, a writable checkout, and a
browser. For Anthropic answers, you also need an approved API key and model. Use a
provider approved to receive the example business data.

The `setup-local`, `start-local`, `stop-local` and `start-phoenix` launchers have been
removed. `ac2 services` is planned to install, start and stop Agent Lab and Phoenix; until
it is released you start each piece yourself, as below. Phoenix has no start command in
this checkout, so the walkthrough runs without traces.

Commands are shown for **Command Prompt (cmd.exe)**. On macOS/Linux, open a bash or zsh
terminal and use `python3` for `python`, `/` for `\`, and `cat` for `type`.

## 1. Install what you will run

1. **The SDK and rmprep.** Install the SDK once with
   `python tools/install_sdk.py --dev --editable` from the repository root (see the
   [README](../README.md#working-on-the-library)). Then install the rmprep workspace into the
   same Python by following [its README](../examples/rmprep/README.md).
2. **Agent Lab**, in an environment of its own, because Chainlit needs MCP 1.x and the agents
   need MCP 2.x. Follow [step 1 of the Agent Lab guide](../packages/agentcraft2-ui/README.md#1-prepare-the-ui-packages).

For Anthropic answers, install the model dependency with
`python -m pip install "agentcraft2-core[anthropic]"` in the agents' Python. For Bedrock, use
`agentcraft2-aws[bedrock]`, which includes `langchain-aws` and boto3.

## 2. Set the model

Create or edit the **rmprep service's `.env`**, `examples\rmprep\agents\rmprep\.env`. Keep
your existing settings; add or update these entries, replacing the API-key placeholder:

```dotenv
AC2_MODEL_PROVIDER=anthropic
AC2_MODEL_ID=claude-sonnet-4-5
ANTHROPIC_API_KEY=your-approved-key
AC2_MODEL_OPTIONS={"anthropic":{"prompt_caching":true,"cache_ttl":"5m"}}
```

Use a model enabled for your account. Keep the file private and out of Git.
For a basic connectivity check, you can keep the generated `fake` model; it
echoes questions. Business memos and natural-language follow-ups need a real model.

For **Amazon Bedrock**, use these model settings instead:

```dotenv
AC2_MODEL_PROVIDER=bedrock
AC2_MODEL_ID=your-enabled-model-or-inference-profile-id
AWS_REGION=your-aws-region
```

Use the AWS credentials or profile provided by your enterprise setup. If your
team supplies a named profile, set `AWS_PROFILE` to that profile. Restart the agent after
changing a setting. Model access and authentication still come from your AWS account. See the
[AWS package guide](../packages/agentcraft2-aws/README.md) for provider options.

## 3. Start the services and open Agent Lab

Start the MCP data server and the agent, each in its own terminal at the repository root
(the [Agent Lab guide](../packages/agentcraft2-ui/README.md#2-start-your-agent-and-register-its-address)
has the commands and explains the registry entry). Then initialise Agent Lab once and start it:

```bat
"%LAB_PYTHON%" -m agentcraft2_ui init --env-file examples\rmprep\agents\rmprep\.env --agent rmprep --artifacts-agent rmprep --allow-url http://127.0.0.1:8000
"%LAB_PYTHON%" -m agentcraft2_ui serve
```

Open [Agent Lab](http://127.0.0.1:8765). In a second terminal at the repository root, read
the local access code:

```bat
type .agentcraft2\ui\access-code
```

Sign in with that code, then select **Choose agent → Start chat with rmprep**. This code
belongs to Agent Lab; it is separate from your model API key.

Local ports are MCP **8100**, rmprep **8000**, Agent Lab **8765**. The MCP URL is
`http://127.0.0.1:8100/mcp`.

### Generate a memo and ask follow-ups

Send these messages one at a time in the **same chat**:

1. `Prepare a meeting memo for Jones Dairy Farm (JONE-0022) as of 2026-08-03.`
2. `Explain the covenant breaches and the change in cash conversion cycle in that memo. Also, how was its wallet share calculated? Use the saved evidence.`
3. `Does the recorded waiver expiry prove the waiver was executed? Explain what the evidence establishes and what remains unknown.`

Check the answer against the supplied snapshot:

| Topic | Expected evidence |
| --- | --- |
| Covenants | DSCR 1.08 against a minimum 1.25; fixed charge coverage 0.96 against a minimum 1.15. |
| Cash conversion cycle | 87 days at 2026-03-31 to 106 days at 2026-06-30: an increase of 19 days. The values alone do not establish causes. |
| Wallet share | Recorded as 40.21%. The source does not supply the numerator, denominator, period or calculation method. |
| Waiver | A recorded expiry date does not prove execution. The answer should preserve that uncertainty. |

The first question retrieves MCP evidence. A follow-up can reuse complete saved
evidence without another MCP call. Model wording and call counts can vary.

### Test client switching and older memos

In the same chat, ask `Prepare a memo for Canada Goose as of 2026-08-03.`, then
`How was the wallet share in that memo calculated?`. The selected client should
be `CANA-0010`, with recorded wallet share **22.55%**. Its calculation method is
also unavailable in the source.

Open **Memos**, select the earlier Jones memo, and expand **Memo details** to copy
its reference. Ask `Using <memo-reference>, explain its covenant breaches.`,
replacing the placeholder with that reference. Selecting a version in the panel
changes the viewer; name its reference in the question to select it for the agent.
Both clients' memo versions remain saved. Reopen this chat from the sidebar to
continue it, or start a new chat for a separate conversation.

### Read the response

Text appears in **Live draft (provisional)** as approved model output arrives.
The panel disappears when the final outcome is ready. Each response keeps token
usage and elapsed time visible, plus cache statistics when reported.
**Logs** provides local diagnostics and saved checkpoint inspection; opening it does not
run the agent again. If a Phoenix server is running on this machine and tracing is on,
**Open trace in Agent Lab** opens that turn's trace (see the [Phoenix trial](phoenix-trial.md)).

Chat presents readable answers without long source references beside every
figure. The original answer and memo documents retain their references for review.
MCP service logs include the query, parameters and record statistics.

Ask `Keep it within 450 words.` to change an answer's length limit. The agent
checks length and exact source references before it saves an answer. A failing
draft can be revised twice; rejected drafts remain in traces rather than
accepted conversation history. The [rmprep README](../examples/rmprep/README.md#3-generate-a-memo-with-your-configured-model)
lists the default limits, how words are counted and every check; see also
[context management](../examples/rmprep/docs/context-management.md).

## 4. Stop and restart

Press **Ctrl+C** in each terminal that runs a service: Agent Lab first, then the agent, then
the MCP server. Their data is preserved. Restart after editing code or environment settings.

| Saved data | Default location under the repository root |
| --- | --- |
| Conversations, answers and memo versions | `.agentcraft2\ui\conversations.sqlite` |
| rmprep checkpoints | `examples\rmprep\agents\rmprep\.agentcraft2\checkpoints.sqlite` |
| Audit and optional model JSONL files | Each service's `.agentcraft2` folder |

For optional file-based model inspection, add
`AC2_MODEL_TRACE_PATH=.agentcraft2/model-trace.jsonl` to rmprep's `.env` and restart.
Capture starts with new turns; retain the file to inspect them later. Library storage paths
such as this one resolve from the service folder.

## Run local checks

Install the development tools once (`python tools/install_sdk.py --dev --editable`), then run
the focused tests:

```bat
cd examples\rmprep
python -m pytest -q
cd ..\..
cd packages\agentcraft2-ui
"%LAB_PYTHON%" -m pytest -q
cd ..\..
```

Run UI tests from the UI package folder so pytest uses that package's configuration.
These tests use local fixtures and scripted models. Full contributor checks are
in the [library README](../README.md#working-on-the-library) and
[Agent Lab README](../packages/agentcraft2-ui/README.md#run-the-checks-and-build-a-wheel).

## Troubleshooting

| Symptom | First step |
| --- | --- |
| Missing package or mismatched Python | Install the SDK and the rmprep workspace into the Python that runs the agent; check your approved pip index and wheels. |
| Login asks for a code | Run `type .agentcraft2\ui\access-code`. Use `127.0.0.1:8765` consistently. |
| The agent answers with `fake` | Check the model settings in rmprep's `.env`. Set provider and model ID along with the key. Clear an unwanted shell override with `set AC2_MODEL_PROVIDER=` (`unset AC2_MODEL_PROVIDER` on macOS/Linux), then restart. A shell that still exports the old `EAP_MODEL_PROVIDER` spelling needs it cleared the same way. |
| A port is in use | Stop the other service with Ctrl+C in its own window. Existing processes are left alone. |
| Cache hit is 0% | Check cache writes. A cold request, changed prompt/context, or expired entry can write a prefix; a later matching request can read it. |
| The answer revises itself | Drafts are provisional until length, citation and evidence checks pass. |
| Wrong memo is discussed | Keep the same chat and name the reference from **Memo details**. Viewing an older version alone does not select its model context. |
| An old layout appears in Chrome | Stop/restart, then reload the tab. Wide tables scroll within their own container. |
| Registry pins or local identity changed | Review the changes, then refresh UI configuration below. Saved conversations remain. |

After reviewing registry or identity changes, stop Agent Lab and refresh its
configuration from the repository root:

```bat
"%LAB_PYTHON%" -m agentcraft2_ui init --config .agentcraft2\ui\config.json --env-file examples\rmprep\agents\rmprep\.env --agent rmprep --artifacts-agent rmprep --allow-url http://127.0.0.1:8000 --overwrite
"%LAB_PYTHON%" -m agentcraft2_ui serve
```

Startup checks saved bindings and preserves existing UI configuration. Changing
the configured identity makes the old identity's conversations inaccessible to
the new identity; it does not delete them.
