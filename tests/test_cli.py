import pytest

from skillship import __version__
from skillship.cli import NOT_IMPLEMENTED, build_parser, main


def test_version_flag_prints_the_package_version(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"skillship {__version__}"


@pytest.mark.parametrize(
    "argv",
    [
        ["lock", "acme"],
        ["lock", "--all", "--check"],
        ["build", "acme", "--out", "out"],
        ["deliver", "acme", "--target", "git", "--dry-run"],
        ["drift", "acme"],
    ],
)
def test_declared_commands_parse_and_report_not_implemented(argv, capsys):
    assert main(argv) == NOT_IMPLEMENTED
    assert f"skillship {argv[0]}: not implemented yet" in capsys.readouterr().err


def test_lock_needs_a_distro_name_or_all():
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args(["lock"])
    assert exit_info.value.code == 2


def test_lock_rejects_a_name_together_with_all():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["lock", "acme", "--all"])
