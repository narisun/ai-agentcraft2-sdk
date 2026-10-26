"""'agentcraft2 check': the gate a change passes before it is shared."""

from __future__ import annotations

from pathlib import Path

import click

from agentcraft2_cli.commands.common import pass_toolbox, workspace_option, workspace_root
from agentcraft2_cli.commands.rules import policy_test
from agentcraft2_cli.diagnostics import service_folder
from agentcraft2_cli.errors import CliError, invocation
from agentcraft2_cli.names import package_name
from agentcraft2_cli.toolbox import Toolbox
from agentcraft2_cli.workspace import WorkspaceAnswers, load_answers

__all__ = ["CHECKS", "check", "evals"]

CHECKS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("lint", "ruff", ("check", ".")),
    ("format", "ruff", ("format", "--check", ".")),
    ("types", "mypy", ()),
    ("tests", "pytest", ("-q",)),
)
"""Each check: its name, the tool module, and the tool's arguments."""


def _require_installed(tools: Toolbox, answers: WorkspaceAnswers) -> None:
    """Stop at once, with the fix, when a service has not been installed.

    The tests import each service as a package. Without 'install' they fail to collect with
    a ModuleNotFoundError that says nothing about what to do.
    """
    missing = sorted(
        name for name in answers.service_names() if not tools.is_installed(package_name(name))
    )
    if missing:
        raise CliError(
            f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} not installed here",
            expected="every service of the workspace installed, so its tests can import it",
            actual=f"not importable: {', '.join(package_name(name) for name in missing)}",
            fix=f"run '{invocation()} install' from the workspace, then run the checks again",
        )


@click.command()
@click.option("--opa", "with_opa", is_flag=True, help="Also check that OPA agrees with the rules.")
@click.option("--keep-going", is_flag=True, help="Run every check even after one fails.")
@click.option(
    "--fix",
    "fix_first",
    is_flag=True,
    help="First fix what ruff can: lint fixes and formatting. Review the changes it makes.",
)
@workspace_option
@click.pass_context
@pass_toolbox
def check(
    tools: Toolbox,
    context: click.Context,
    with_opa: bool,
    keep_going: bool,
    fix_first: bool,
    workspace: Path | None,
) -> None:
    """Run what CI runs: lint, format, types, tests and the policy samples.

    The same command runs on a developer's machine and in the workspace's CI
    workflow, so a change that passes here passes there.

    \b
    Examples:
      ac2 check                 stop at the first failing check
      ac2 check --keep-going    run every check and list all that fail
      ac2 check --opa           also check that OPA agrees with the in-process rules
    """
    root = workspace_root(workspace)
    _require_installed(tools, load_answers(root))
    if fix_first:
        for fixing in (("check", "--fix", "."), ("format", ".")):
            click.echo(f"== fix: python -m ruff {' '.join(fixing)}")
            # What ruff cannot fix is reported by the checks below.
            tools.run_tool("ruff", fixing, root)
    results: list[tuple[str, bool]] = []
    for name, module, arguments in CHECKS:
        click.echo(f"== {name}: python -m {module} {' '.join(arguments)}".rstrip())
        passed = tools.run_tool(module, arguments, root) == 0
        results.append((name, passed))
        if not passed and not keep_going:
            break
    if all(passed for _, passed in results) or keep_going:
        click.echo("== policy samples")
        try:
            context.invoke(policy_test, with_opa=with_opa, bundle=None, workspace=root)
        except CliError as problem:
            click.echo(f"agentcraft2: error: {problem}", err=True)
            results.append(("policy samples", False))
        else:
            results.append(("policy samples", True))
    click.echo("")
    for name, passed in results:
        click.echo(f"  {'ok  ' if passed else 'FAIL'}  {name}")
    failed = [name for name, passed in results if not passed]
    if failed:
        skipped = [name for name, *_ in CHECKS if name not in {done for done, _ in results}]
        if not keep_going:
            skipped.append("policy samples")  # they run only when nothing before them failed
        fix = "read the output of each failed check above"
        if skipped:
            fix += (
                f"; {', '.join(skipped)} did not run: add --keep-going to run every check"
                " after a failure"
            )
        fix += (
            "; for lint and format problems, add --fix to let ruff correct them"
            if {"lint", "format"} & set(failed)
            else ""
        )
        raise CliError(
            f"{len(failed)} check{'s' if len(failed) != 1 else ''} failed: {', '.join(failed)}",
            fix=fix,
        )
    click.echo("Every check passed.")


@click.command("eval", short_help="Run the evals of a service with a real model.")
@click.argument("service", required=False)
@click.option(
    "--accept",
    is_flag=True,
    help="After the evals pass, save the report as the service's evals/baseline.json.",
)
@workspace_option
@pass_toolbox
def evals(tools: Toolbox, service: str | None, accept: bool, workspace: Path | None) -> None:
    """Run the evals of SERVICE, or of every service: its tests marked eval, with a real model.

    An eval skips itself until a real model is set in the service's .env. Each
    writes a report to the service's .agentcraft2/eval-report.json.
    --accept saves the passing report as evals/baseline.json; later runs fail
    on regressions against it. --accept still enforces the absolute bar. While
    the pytest run is in flight it leaves a marker beside the eval report. The
    generated test skips only the regression gate against the existing baseline
    when that marker is there, so a deliberate change moves the baseline instead
    of deadlocking on it. The marker is removed once the run ends.
    """
    if accept and not service:
        raise CliError("--accept needs a service", fix="agentcraft2 eval SERVICE --accept")
    root = workspace_root(workspace)
    target = service_folder(root, load_answers(root), service) if service else root
    arguments = ("-m", "eval", "-q", "-rs", target.relative_to(root).as_posix() if service else ".")
    click.echo(f"== evals: python -m pytest {' '.join(arguments)}")
    accept_marker = target / ".agentcraft2" / "eval-accept.flag"
    # A marker left by a killed --accept run must not skip later regression gates. Each
    # generated test looks for the marker in its own service folder, so with no SERVICE
    # every service folder is cleared, not the workspace root.
    for stale in [accept_marker] if service else _service_markers(root):
        stale.unlink(missing_ok=True)
    report = target / ".agentcraft2" / "eval-report.json"
    if accept:
        # An eval that skips itself writes no report. A report left by an earlier run
        # must not be mistaken for this run's and saved as the baseline.
        report.unlink(missing_ok=True)
        accept_marker.parent.mkdir(parents=True, exist_ok=True)
        accept_marker.touch()
    try:
        code = tools.run_tool("pytest", arguments, root)
    finally:
        if accept:
            accept_marker.unlink(missing_ok=True)
    # pytest says 5 when no test was collected: a service with no evals yet.
    if code == _NO_TESTS:
        raise CliError(
            f"there are no evals in {target.relative_to(root).as_posix() or 'this workspace'}",
            fix="add a test marked @pytest.mark.eval; 'agentcraft2 new agent' writes one",
        )
    if code != 0:
        raise CliError("the evals did not meet their bar", fix="read the report above")
    if accept:
        if not report.is_file():
            raise CliError(
                f"this run wrote no report at {report.relative_to(root).as_posix()}",
                expected="an eval that ran against a real model and wrote its report",
                actual="no report; an eval skips itself while the service's .env names the "
                "fake model provider",
                fix="set a real model provider and model ID in the service's .env, "
                "then run 'agentcraft2 eval SERVICE --accept' again",
            )
        baseline = target / "evals" / "baseline.json"
        baseline.parent.mkdir(parents=True, exist_ok=True)
        baseline.write_bytes(report.read_bytes())
        click.echo(f"baseline accepted: {baseline.relative_to(root).as_posix()}")


_NO_TESTS = 5


def _service_markers(root: Path) -> list[Path]:
    """Every accept marker a killed ``--accept`` run could have left in a service folder."""
    return sorted(root.glob("*/*/.agentcraft2/eval-accept.flag"))
