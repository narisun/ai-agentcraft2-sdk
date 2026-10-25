"""Opt-in Phoenix tracing settings, separate from service and operational telemetry.

Phoenix is a tool for the developer's own machine. Traces go only to a Phoenix that
listens on a loopback address, which needs no key, so no setting can point them at
another host and none carries a credential for one.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from urllib.parse import urlsplit

from agentcraft2_core.config.bindings import PREFIX, legacy_spelling
from agentcraft2_core.config.deprecations import log_deprecation
from agentcraft2_core.config.sources import (
    DotenvConfigSource,
    EnvironConfigSource,
    MappingConfigSource,
)
from agentcraft2_core.contracts import ConfigSource, ConfigurationError, DeploymentEnv

PHOENIX_LABELS = MappingProxyType(
    {
        "enabled": "AC2_PHOENIX_ENABLED",
        "capture_content": "AC2_PHOENIX_CAPTURE_CONTENT",
        "endpoint": "PHOENIX_COLLECTOR_ENDPOINT",
        "project": "PHOENIX_PROJECT_NAME",
    }
)
"""Variable names by the short label generated documents refer to them by."""

PHOENIX_VARIABLES = tuple(PHOENIX_LABELS.values())

REMOVED_PHOENIX_VARIABLES = MappingProxyType(
    {
        "PHOENIX_API_KEY": (
            "PHOENIX_API_KEY is no longer used: traces go only to a Phoenix on this machine, "
            "which needs no key. Remove it."
        ),
        "AC2_PHOENIX_ENV_FILE": (
            "AC2_PHOENIX_ENV_FILE is no longer read: it carried settings for a Phoenix on "
            "another host. Put the tracing settings in the service's .env."
        ),
    }
)
"""Variables an earlier release read, with what to tell someone who still sets one.

They name the settings of a Phoenix on another host. They are not read, so a value left in
a file can neither send traces away nor be sent anywhere; setting one only earns the message.
"""

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

_Layers = Sequence[tuple[str, ConfigSource]]


@dataclass(frozen=True)
class PhoenixSettings:
    """An OTLP/HTTP origin on this machine and a project; content capture is an opt-in.

    Attributes:
        base_url: The origin of the local Phoenix.
        project_name: The Phoenix project that groups the spans. Empty means the name of the
            service, see :meth:`project_for`.
        capture_content: Whether model messages and tool arguments and results are traced.
    """

    base_url: str = "http://127.0.0.1:6006"
    project_name: str = ""
    capture_content: bool = False

    def __post_init__(self) -> None:
        try:
            parts = urlsplit(self.base_url)
            _ = parts.port
        except ValueError as error:
            raise ConfigurationError("PHOENIX_COLLECTOR_ENDPOINT must be a valid origin") from error
        if (
            not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
            or parts.path not in {"", "/"}
            or re.search(r"[\s\x00-\x1f\x7f<>\\()]", self.base_url)
            or parts.scheme != "http"
            or parts.hostname not in LOOPBACK_HOSTS
        ):
            raise ConfigurationError(
                "PHOENIX_COLLECTOR_ENDPOINT must be an http origin on this machine",
                expected="http://localhost, http://127.0.0.1 or http://[::1] with an optional port",
                fix=(
                    "Phoenix runs on the developer's machine and traces are never sent to "
                    "another host; use PHOENIX_COLLECTOR_ENDPOINT only to pick a local port"
                ),
            )
        if self.project_name and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.project_name):
            raise ConfigurationError(
                "PHOENIX_PROJECT_NAME must contain 1-128 letters, digits, underscores or hyphens"
            )

    def project_for(self, application: str) -> str:
        """The Phoenix project for a service: the configured name, else the service's own."""
        if self.project_name:
            return self.project_name
        return re.sub(r"[^A-Za-z0-9_-]", "-", application)[:128] or "agentcraft2"

    def trace_url(self, trace_id: str) -> str:
        """Link to a trace in the local Phoenix."""
        return f"{self.base_url.rstrip('/')}/redirects/traces/{trace_id}"


def require_local_tracing(settings: PhoenixSettings | None, deployment_env: DeploymentEnv) -> None:
    """Refuse Phoenix tracing outside a developer's machine.

    Args:
        settings: The resolved tracing settings, or ``None`` when tracing is off.
        deployment_env: Where the service runs.

    Raises:
        ConfigurationError: If tracing is on and the deployment environment is not local.
    """
    if settings is not None and deployment_env is not DeploymentEnv.LOCAL:
        raise ConfigurationError(
            "Phoenix tracing is a tool for a developer's machine, and a deployed service "
            "does not use it",
            expected="tracing off when the deployment environment is not local",
            actual=f"on in {deployment_env.value}",
            fix=f"set {PHOENIX_LABELS['enabled']}=false; a deployed service reports through "
            "its telemetry setting",
        )


def _first(layers: _Layers, variable: str) -> tuple[str, str] | None:
    """The first layer's non-blank value of a variable, and where it came from."""
    # A blank value is not set, as everywhere else in the configuration: a compose file
    # that passes an unset host variable through as "AC2_PHOENIX_ENABLED=" must not hide
    # the value in the .env file behind it.
    return next(
        (
            (value.strip(), f"{label}: {variable}")
            for label, layer in layers
            if (value := layer.get(variable)) is not None and value.strip()
        ),
        None,
    )


def _lookup(
    layers: _Layers, name: str, deprecations: list[str] | None = None
) -> tuple[str, str] | None:
    """Find a variable's value and origin in the first layer that sets it.

    An ``AC2_`` name is also read in its ``EAP_`` spelling, which keeps working
    for one minor release. Equal values in both spellings coexist silently. A
    different value is a conflict, wherever each spelling is set, because
    guessing which one was meant is the hazard. A legacy spelling that supplies
    the value is logged as deprecated, and its message is added to
    ``deprecations`` when the caller passes a list to collect them in.

    Raises:
        ConfigurationError: If the two spellings carry different values.
    """
    current = _first(layers, name)
    if not name.startswith(PREFIX):
        return current
    old_name = legacy_spelling(name)
    old = _first(layers, old_name)
    if old is None:
        return current
    if current is not None:
        if current[0] != old[0]:
            raise ConfigurationError(
                f"{old_name} is deprecated and conflicts with {name}; set only {name}"
            )
        return current
    message = f"{old_name} is deprecated; use {name} instead"
    log_deprecation(message)
    if deprecations is not None:
        deprecations.append(message)
    return old


def _still_set(layers: _Layers) -> tuple[str, ...]:
    """The message for each removed variable that a layer still sets, in either spelling."""
    messages: list[str] = []
    for name, message in REMOVED_PHOENIX_VARIABLES.items():
        spellings = (name, legacy_spelling(name)) if name.startswith(PREFIX) else (name,)
        messages.extend(
            message.replace(name, spelling)
            for spelling in spellings
            if _first(layers, spelling) is not None
        )
    return tuple(messages)


def load_phoenix_settings(
    dotenv_path: Path | None = Path(".env"),
    *,
    source: ConfigSource | None = None,
    environment: ConfigSource | None = None,
) -> PhoenixSettings | None:
    """Resolve environment > service settings without changing the process.

    A launcher can supply its planned child ``environment`` instead of changing
    ``os.environ``. ``source`` supplies a complete configuration and bypasses
    file layering; supply only one of these overrides.
    """
    resolution = resolve_phoenix_settings(dotenv_path, source=source, environment=environment)
    for message in resolution.removed:
        log_deprecation(message)
    return resolution.settings


@dataclass(frozen=True)
class PhoenixResolution:
    """Validated optional settings and an explanation of where each came from.

    Attributes:
        settings: The validated settings, or ``None`` when tracing is off.
        shown: Each variable's value as it may be shown.
        origins: Where each variable's value came from.
        warnings: One message for each legacy spelling that supplied a value, such
            as ``"EAP_PHOENIX_ENABLED is deprecated; use AC2_PHOENIX_ENABLED instead"``.
            They are also logged, once per process, through the library logger.
        removed: One message for each variable that an earlier release read and this
            one does not, such as ``PHOENIX_API_KEY``, that a layer still sets.
    """

    settings: PhoenixSettings | None
    shown: Mapping[str, str]
    origins: Mapping[str, str]
    warnings: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()


def resolve_phoenix_settings(
    dotenv_path: Path | None = Path(".env"),
    *,
    source: ConfigSource | None = None,
    environment: ConfigSource | None = None,
) -> PhoenixResolution:
    """Resolve the runtime settings for diagnostics or a planned child process."""
    if source is not None and environment is not None:
        raise ValueError("Supply either source or environment to load_phoenix_settings")
    if source is None:
        environment = environment if environment is not None else EnvironConfigSource()
        service = DotenvConfigSource(dotenv_path) if dotenv_path else MappingConfigSource({})
        layers: list[tuple[str, ConfigSource]] = [
            ("process environment", environment),
            (str(dotenv_path.resolve()) if dotenv_path else "service settings", service),
        ]
    else:
        layers = [("injected source", source)]
    defaults = {
        "AC2_PHOENIX_ENABLED": "false",
        "AC2_PHOENIX_CAPTURE_CONTENT": "false",
        "PHOENIX_COLLECTOR_ENDPOINT": "http://127.0.0.1:6006",
        "PHOENIX_PROJECT_NAME": "",
    }
    values, origins = {}, {}
    deprecations: list[str] = []
    for name in PHOENIX_VARIABLES:
        found = _lookup(layers, name, deprecations)
        values[name], origins[name] = found or (defaults.get(name, ""), "default")
    removed = _still_set(layers)

    def flag(name: str) -> bool:
        value = values[name] or "false"
        if value.lower() not in {"true", "false"}:
            raise ConfigurationError(f"{name} must be true or false")
        return value.lower() == "true"

    settings = (
        PhoenixSettings(
            base_url=values["PHOENIX_COLLECTOR_ENDPOINT"] or defaults["PHOENIX_COLLECTOR_ENDPOINT"],
            project_name=values["PHOENIX_PROJECT_NAME"],
            capture_content=flag("AC2_PHOENIX_CAPTURE_CONTENT"),
        )
        if flag("AC2_PHOENIX_ENABLED")
        else None
    )
    return PhoenixResolution(
        settings,
        MappingProxyType(values),
        MappingProxyType(origins),
        tuple(deprecations),
        removed,
    )


def phoenix_env_example(guide: str = "docs/phoenix-trial.md") -> str:
    """Return commented defaults for opt-in tracing, for generated and example ``.env`` files.

    Args:
        guide: The path, from the file that carries the block, of the tracing guide.
    """
    defaults = PhoenixSettings()
    examples = {
        "enabled": "false",
        "capture_content": "false",
        "endpoint": defaults.base_url,
        "project": "my-agent",
    }
    return f"\n# Optional Phoenix tracing on this machine: see {guide}.\n" + "".join(
        f"# {PHOENIX_LABELS[label]}={value}\n" for label, value in examples.items()
    )
