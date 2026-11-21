"""`ops` command line entry point."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console

from opsagent import __version__
from opsagent.bootstrap import StartupError, has_failure, run_doctor, run_tui
from opsagent.infra import ConfigError, configure_logging, load_settings
from opsagent.persistence import current_revision, head_revision, upgrade

app = typer.Typer(
    help="Terminal-native personal operations agent.",
    no_args_is_help=False,
    add_completion=False,
)
db_app = typer.Typer(help="Database commands.", no_args_is_help=True)
app.add_typer(db_app, name="db")

console = Console()
_ICON = {"ok": "[green]ok[/]", "warn": "[yellow]warn[/]", "fail": "[red]FAIL[/]"}


def _version(value: bool) -> None:
    if value:
        console.print(f"ops {__version__}")
        raise typer.Exit


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show version.")
    ] = False,
    demo: Annotated[
        bool, typer.Option("--demo", help="Replay scripted scenarios instead of calling a model.")
    ] = False,
) -> None:
    if ctx.invoked_subcommand is None:
        try:
            run_tui(load_settings(), demo=demo)
        except StartupError as exc:
            console.print(f"[red]cannot start:[/] {exc}")
            raise typer.Exit(1) from exc


@app.command()
def doctor() -> None:
    """Check the database, extensions, schema, models, and credentials."""
    try:
        settings = load_settings()
    except ConfigError as exc:
        console.print(f"[red]config error:[/] {exc}")
        raise typer.Exit(2) from exc
    configure_logging(settings.log_dir, settings.log_level)
    results = run_doctor(settings)
    for check in results:
        console.print(f"{_ICON[check.status]:>14}  {check.name:<13} {check.detail}")
        if check.fix and check.status != "ok":
            console.print(f"{'':>14}  {'':<13} fix: {check.fix}")
    if has_failure(results):
        raise typer.Exit(1)


@db_app.command("migrate")
def db_migrate() -> None:
    """Apply database migrations and create LangGraph checkpoint tables."""
    settings = load_settings()
    upgrade(settings.database_url)
    console.print(f"schema at revision {current_revision(settings.database_url)}")


@db_app.command("status")
def db_status() -> None:
    """Show the applied and expected schema revision."""
    settings = load_settings()
    current, head = current_revision(settings.database_url), head_revision()
    console.print(f"applied: {current or 'none'}  expected: {head}")
    if current != head:
        raise typer.Exit(1)
