# 0004: Tracing configuration ownership

## Context

`config/langfuse.py` and `config/phoenix.py` resolve `LANGFUSE_*`, `PHOENIX_*`
and `EAP_LANGFUSE_ENABLED` through dedicated loaders, outside the binding
table in `config/bindings.py`. The October `doctor` blind spot came from
this parallel path; the fix shared a loader for that one check, but the
second path itself remains, and the binding table's "unknown `EAP_` names
fail startup" guarantee does not cover these variables. Tracing code spans
`config/`, `observability/` and `integrations/langgraph/`.

## Decision

Keep the vendors' documented names (`LANGFUSE_BASE_URL`, `PHOENIX_API_KEY`
and the rest) and register them in the binding table as listed third-party
reads, the way `AWS_PROFILE`, `AWS_REGION`, `AWS_CA_BUNDLE` and
`AWS_EXECUTION_ENV` already are; `OTEL_*` variables are read directly by the
OpenTelemetry SDK through `configure_telemetry()` and stay outside the table.
Keep `EAP_LANGFUSE_ENABLED`, `EAP_LANGFUSE_CAPTURE_CONTENT`,
`EAP_LANGFUSE_ENV_FILE`, `EAP_PHOENIX_ENABLED`, `EAP_PHOENIX_CAPTURE_CONTENT`
and `EAP_PHOENIX_ENV_FILE` as bound, `EAP_`-prefixed variables. Every
optional integration must resolve its settings through one loader shared by
startup, `doctor` and `config explain` — the Langfuse fix generalized as a
standing rule, not a one-off.

### Amendment, 2026-10-18: Langfuse removed

The Context and the Decision above stay as written; they record the decision
as it was made. This amendment changes what is in force.

Langfuse support was removed, and Phoenix is the only agent-trace backend.
`config/langfuse.py`, the Langfuse integration module, `EAP_LANGFUSE_ENABLED`,
`EAP_LANGFUSE_CAPTURE_CONTENT` and `EAP_LANGFUSE_ENV_FILE` no longer exist, so
the Consequences below apply to Phoenix alone. A service `.env` that still sets
an `EAP_LANGFUSE_*` variable fails startup as an unknown variable. Bare
`LANGFUSE_*` names are inert: nothing reads them.

The standing rule now reads for Phoenix. `resolve_phoenix_settings` is the one
loader for `EAP_PHOENIX_ENABLED`, `EAP_PHOENIX_CAPTURE_CONTENT`,
`EAP_PHOENIX_ENV_FILE`, `PHOENIX_COLLECTOR_ENDPOINT`, `PHOENIX_PROJECT_NAME` and
`PHOENIX_API_KEY`. Startup, `doctor`, `config explain` and the local launcher
all resolve through it, so a check shows what startup will use.
`PHOENIX_API_KEY` is read through the same layering (process environment,
separate tracing file, service `.env`), not through the secrets provider;
deployments inject it as a task-definition secret. `agent_tracing` is the single
composition-boundary name and `run_agent` composes it, so a service honors
`EAP_PHOENIX_ENABLED` without tracing code. Adding a backend later is a change
inside that module, not a registry.

Registering the Phoenix variables in the binding table, with the loader reading
through it, is still future work. Until then the resolver accepts the
`EAP_PHOENIX_*` names, and the vendor-named `PHOENIX_*` names stay outside the
"unknown `EAP_` names fail startup" guarantee.

### Amendment, 2026-10-25: Phoenix is for the developer's machine

The Context and the Decision above stay as written; they record the decision
as it was made. This amendment changes what is in force.

A developer's `.env` can be shared, copied or set by a pipeline, and a trace
carries conversation metadata. Nothing in the library may therefore send a trace
to a host the developer did not start. `PHOENIX_COLLECTOR_ENDPOINT` must be an
`http` origin on `localhost`, `127.0.0.1` or `[::1]`; any other value stops
startup. `PHOENIX_API_KEY` is no longer read and no `Authorization` header is
sent, because the local Phoenix takes none. `EAP_PHOENIX_ENV_FILE` and
`AC2_PHOENIX_ENV_FILE` are no longer read either: the file existed to keep that
key private. A value still set in a file or the process is told about once, by
`doctor`, `config explain` and the startup log, without showing it. The
resolver keeps both names known so that the loader's message, and not an
unknown-variable failure, is what a developer sees.

Phoenix tracing is refused when the deployment environment is `dev` or `prod`
(`require_local_tracing`, applied by startup and `doctor`). A deployed service
reports through its telemetry setting. This also retires the earlier production
check on content capture, which is now covered because tracing itself is off
there. `PHOENIX_PROJECT_NAME` defaults to the service's name, so agents do not
share one project unless a developer sets it. Agent Lab accepts a Phoenix trace
link only when it is loopback `http`.

The loader now resolves four variables: `AC2_PHOENIX_ENABLED`,
`AC2_PHOENIX_CAPTURE_CONTENT`, `PHOENIX_COLLECTOR_ENDPOINT` and
`PHOENIX_PROJECT_NAME`. Registering them in the binding table is still future
work.

## Consequences

Langfuse and Phoenix need their variables registered and their loaders
adapted to read through the binding table; no behavioral change to current
deployments. Future optional integrations must register their variables and
share a loader across startup, doctor and config explain before shipping, or
they inherit the same blind spot. Consolidating the tracing modules across
`config/`, `observability/` and `integrations/langgraph/` is possible later
but is not decided here.

## Owner

Library maintainers.
