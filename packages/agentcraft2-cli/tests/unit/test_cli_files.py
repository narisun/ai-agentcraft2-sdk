"""A file a command reads that cannot be read says why, instead of a traceback or a guess."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentcraft2_cli.errors import CliError
from agentcraft2_cli.files import read_text
from agentcraft2_cli.policy import load_samples
from agentcraft2_cli.scaffold import LOCK_FILE, Scaffolder
from agentcraft2_cli.workspace import load_answers


def test_a_missing_file_names_the_file_and_the_operating_system_reason(tmp_path: Path) -> None:
    with pytest.raises(CliError) as raised:
        read_text(tmp_path / "gone.toml", what="the workspace file")
    message = raised.value.message
    assert "the workspace file" in message
    assert str(tmp_path / "gone.toml") in message
    assert "FileNotFoundError" in message
    assert isinstance(raised.value.__cause__, FileNotFoundError)


def test_bytes_that_are_not_utf8_say_which_byte_and_where(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_bytes(b"name = \xff\n")
    with pytest.raises(CliError) as raised:
        read_text(path, what="the answers file")
    assert "UnicodeDecodeError" in raised.value.message
    assert "0xff" in raised.value.message


def test_a_byte_order_mark_is_accepted_because_powershell_writes_one(tmp_path: Path) -> None:
    path = tmp_path / "agentcraft2.toml"
    path.write_bytes(b"\xef\xbb\xbfname = 'x'\n")
    assert read_text(path, what="the workspace file") == "name = 'x'\n"


def test_loading_a_workspace_file_that_is_a_folder_says_so(tmp_path: Path) -> None:
    (tmp_path / "agentcraft2.toml").mkdir()
    with pytest.raises(CliError, match=r"the workspace file .* cannot be read \(\w*Error"):
        load_answers(tmp_path)


def test_a_samples_file_that_cannot_be_read_gives_the_reason(tmp_path: Path) -> None:
    with pytest.raises(CliError) as raised:
        load_samples(tmp_path / "policy-samples.yaml")
    assert "FileNotFoundError" in raised.value.message
    assert raised.value.fix == "there are no samples to try without it"


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        ("{not json", "JSONDecodeError"),
        ('{"files": "x"}', "ValueError"),  # not a table of file names and hashes
        ("[]", "TypeError"),  # a JSON value of the wrong kind
    ],
)
def test_a_lock_file_that_is_wrong_says_what_is_wrong_not_a_traceback(
    tmp_path: Path, content: str, reason: str
) -> None:
    (tmp_path / LOCK_FILE).write_text(content, encoding="utf-8")
    with pytest.raises(CliError) as raised:
        Scaffolder._read_lock(tmp_path)
    assert "is not a lock file" in raised.value.message
    assert reason in raised.value.message
    assert raised.value.fix == "delete it to start a new record"


def test_link_takes_force_like_the_other_commands_that_name_it_in_their_conflict_message() -> None:
    from agentcraft2_cli.commands.create import link

    assert "--force" in [option.opts[0] for option in link.params if option.opts]


def test_generated_text_that_does_not_parse_says_what_and_where() -> None:
    from pathlib import PurePosixPath

    from agentcraft2_cli.render import validate_files

    with pytest.raises(CliError) as raised:
        validate_files({PurePosixPath("a/b.py"): "x = (\n"})
    assert raised.value.message == "generated a/b.py is not valid py"
    assert "line" in str(raised.value.actual)
    with pytest.raises(CliError) as raised:
        validate_files({PurePosixPath("a.yaml"): "a: [1, 2\nb: 3\n"})
    assert "line" in str(raised.value.actual)
    assert "while" not in str(raised.value.actual)  # the problem, not PyYAML's context sentence
