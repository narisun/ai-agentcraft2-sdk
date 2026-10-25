"""The ``agentcraft2`` command line: one group, and the commands it is made of."""

from __future__ import annotations

import click

from agentcraft2_cli import __version__, commands
from agentcraft2_cli.errors import set_trace

__all__ = ["cli"]

_CONTEXT = {"help_option_names": ["-h", "--help"]}

_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Start", ("init", "new", "link", "install")),
    ("Verify", ("doctor", "config", "check", "eval", "policy", "graph")),
    ("Run and watch", ("run", "services", "logs", "errors")),
    ("Keep and ship", ("registry", "update", "deploy")),
)
"""The commands, by what a developer is doing, in the order they are usually needed."""


class _SectionedGroup(click.Group):
    """A group whose help lists its commands by what they are for, not alphabetically."""

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        shown: set[str] = set()
        for title, names in _SECTIONS:
            rows = []
            for name in names:
                command = self.get_command(ctx, name)
                if command is None or command.hidden:
                    continue
                shown.add(name)
                rows.append((name, command.get_short_help_str(limit=formatter.width)))
            if rows:
                with formatter.section(title):
                    formatter.write_dl(rows)
        rest = [
            (name, command.get_short_help_str(limit=formatter.width))
            for name in self.list_commands(ctx)
            if name not in shown and (command := self.get_command(ctx, name)) is not None
        ]
        if rest:
            with formatter.section("More"):
                formatter.write_dl(rest)


@click.group(cls=_SectionedGroup, context_settings=_CONTEXT, no_args_is_help=True)
@click.version_option(__version__, "-V", "--version")
@click.option(
    "-t",
    "--trace",
    is_flag=True,
    help="Also print the stack trace of a mistake in the command line. Failures from deeper "
    "down always show theirs.",
)
def cli(trace: bool) -> None:
    """Create, check, run and deploy a workspace of agents and MCP servers.

    'ac2' and 'agentcraft2' are the same command. Every command writes working code
    with its tests, which run with no account and no network. A failure shows the
    exact message, what was expected and found, how to fix it, and the full stack
    trace. Nothing is hidden from you on your machine; nothing sensitive is written
    to a log on a server.

    \b
    A first session:
      ac2 init my-workspace        create a workspace
      cd my-workspace
      ac2 new agent helper         add an agent (ac2 new mcp adds a tool server)
      ac2 install                  install every service with its tests
      ac2 doctor                   verify the configuration of everything
      ac2 run helper               start the agent
      ac2 logs helper              read its log
      ac2 errors                   list what failed, with the stack traces

    \b
    Where to look:
      ac2 <command> --help         every option, with examples
      ac2 config explain helper    each setting and where it came from
      ac2 config options model     what an adapter accepts, with defaults
    """
    set_trace(trace)


for _name in commands.__all__:
    cli.add_command(getattr(commands, _name))
