"""Tests for the monet CLI commands."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from monet.cli import cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_cli_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "worker" in result.output
    assert "register" in result.output
    assert "server" in result.output


def test_worker_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["worker", "--help"])
    assert result.exit_code == 0
    assert "--pool" in result.output
    assert "--concurrency" in result.output


def test_register_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["register", "--help"])
    assert result.exit_code == 0
    assert "--server-url" in result.output


def test_server_help(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["server", "--help"])
    assert result.exit_code == 0
    assert "--port" in result.output


def test_register_requires_server_url(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["register", "--path", "."])
    assert result.exit_code != 0
