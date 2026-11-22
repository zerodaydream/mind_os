"""`ops` command line entry point."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console

from opsagent import __version__
from opsagent.bootstrap import StartupError, has_failure, run_doctor, run_tui
from opsagent.domain import PolicyDecision
from opsagent.infra import ConfigError, configure_logging, load_settings
from opsagent.persistence import current_revision, head_revision, upgrade
from opsagent.tools import PolicyConfig, ToolPolicy

app = typer.Typer(
    help="Terminal-native personal operations agent.",
    no_args_is_help=False,
    add_completion=False,
)
db_app = typer.Typer(help="Database commands.", no_args_is_help=True)
app.add_typer(db_app, name="db")
policy_app = typer.Typer(help="Approval policy configuration commands.", no_args_is_help=True)
app.add_typer(policy_app, name="policy")

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


@app.command()
def resume(
    session_id: Annotated[
        str | None, typer.Argument(help="Session/thread ID to resume.")
    ] = None,
    demo: Annotated[
        bool, typer.Option("--demo", help="Replay scripted scenarios instead of calling a model.")
    ] = False,
) -> None:
    """Resume an existing session or pick from resumable sessions."""
    try:
        run_tui(load_settings(), demo=demo)
    except StartupError as exc:
        console.print(f"[red]cannot resume:[/] {exc}")
        raise typer.Exit(1) from exc


@policy_app.command("show")
def policy_show() -> None:
    """Show active approval policy configuration."""
    settings = load_settings()
    policy_file = settings.config_dir / "policy.toml"
    if policy_file.is_file():
        cfg = PolicyConfig.from_toml(policy_file.read_text("utf-8"))
        console.print(f"Policy loaded from {policy_file}")
    else:
        cfg = PolicyConfig()
        console.print("Using compiled default policy (no policy.toml)")
    console.print(f"  R0 auto-allow: {cfg.r0_auto}")
    console.print(f"  R1 auto-allow: {cfg.r1_auto}")
    console.print(f"  R2 require approval: {cfg.r2_requires_approval}")
    console.print(f"  R3 require approval: {cfg.r3_requires_approval}")
    console.print(f"  R4 require approval: {cfg.r4_requires_approval}")
    if cfg.overrides:
        console.print("\nTool overrides (tightened):")
        for o in cfg.overrides:
            console.print(f"  {o.tool}: {o.decision.value}")
    else:
        console.print("\nNo tool overrides configured.")


@policy_app.command("tighten")
def policy_tighten(
    tool: Annotated[str, typer.Argument(help="Tool name to tighten policy for.")],
    deny: Annotated[
        bool,
        typer.Option("--deny", help="Deny tool completely instead of requiring approval."),
    ] = False,
) -> None:
    """Tighten policy for a tool (require approval or deny)."""
    settings = load_settings()
    policy_file = settings.config_dir / "policy.toml"
    cfg = (
        PolicyConfig.from_toml(policy_file.read_text("utf-8"))
        if policy_file.is_file()
        else PolicyConfig()
    )
    decision = PolicyDecision.DENY if deny else PolicyDecision.REQUIRE_APPROVAL
    new_overrides = [o for o in cfg.overrides if o.tool != tool]
    new_overrides.append(ToolPolicy(tool=tool, decision=decision))
    cfg.overrides = new_overrides
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    policy_file.write_text(cfg.to_toml(), encoding="utf-8")
    console.print(f"Updated policy for '{tool}' -> {decision.value} in {policy_file}")


@policy_app.command("reset")
def policy_reset(
    tool: Annotated[str, typer.Argument(help="Tool name to reset override for.")],
) -> None:
    """Reset policy override for a tool to defaults."""
    settings = load_settings()
    policy_file = settings.config_dir / "policy.toml"
    if not policy_file.is_file():
        console.print(f"No override found for '{tool}'.")
        return
    cfg = PolicyConfig.from_toml(policy_file.read_text("utf-8"))
    cfg.overrides = [o for o in cfg.overrides if o.tool != tool]
    policy_file.write_text(cfg.to_toml(), encoding="utf-8")
    console.print(f"Reset policy for '{tool}' in {policy_file}")
