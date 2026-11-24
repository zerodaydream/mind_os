"""`ops` command line entry point."""

from __future__ import annotations

import asyncio
import webbrowser
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from opsagent import __version__
from opsagent.bootstrap import AuthKit, StartupError, build_auth, has_failure, run_doctor, run_tui
from opsagent.domain import GEMINI_API_KEY, LANGSMITH_API_KEY, AuthError, PolicyDecision
from opsagent.infra import ConfigError, Settings, SystemClock, configure_logging, load_settings
from opsagent.integrations.auth import CAPABILITIES, KeychainSecretStore, scopes_for, sign_in
from opsagent.persistence import (
    AuditRepository,
    current_revision,
    head_revision,
    open_pool,
    upgrade,
)
from opsagent.tools import PolicyConfig, PolicyError, load_policy, save_policy

app = typer.Typer(
    help="Terminal-native personal operations agent.",
    no_args_is_help=False,
    add_completion=False,
)
db_app = typer.Typer(help="Database commands.", no_args_is_help=True)
app.add_typer(db_app, name="db")
policy_app = typer.Typer(help="Approval policy configuration commands.", no_args_is_help=True)
app.add_typer(policy_app, name="policy")

auth_app = typer.Typer(help="Connect and disconnect accounts.", no_args_is_help=True)
app.add_typer(auth_app, name="auth")

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
    demo: Annotated[
        bool, typer.Option("--demo", help="Replay scripted scenarios instead of calling a model.")
    ] = False,
) -> None:
    """Open the TUI and pick up runs left waiting on an approval.

    Approvals still pending reappear as dialogs; decisions made before the app closed are
    delivered to their paused runs; approvals that expired in the meantime are reported.
    """
    try:
        run_tui(load_settings(), demo=demo)
    except StartupError as exc:
        console.print(f"[red]cannot resume:[/] {exc}")
        raise typer.Exit(1) from exc


def _policy_file() -> tuple[Settings, Path]:
    settings = load_settings()
    return settings, settings.config_dir / "policy.toml"


def _read_policy(policy_file: Path) -> PolicyConfig:
    try:
        return load_policy(policy_file)
    except PolicyError as exc:
        console.print(f"[red]{policy_file} is not valid:[/] {exc}")
        raise typer.Exit(2) from exc


def _record_policy_change(settings: Settings, tool: str, change: str, detail: str) -> None:
    """Policy changes are audited (ARCHITECTURE 5.2). If the audit cannot be written, stop."""

    async def write() -> None:
        pool = await open_pool(settings.database_url, max_size=1)
        try:
            await AuditRepository(pool).append(
                event_type="policy.changed",
                actor="USER",
                subject_type="policy",
                subject_id=tool,
                data={"change": change, "detail": detail},
            )
        finally:
            await pool.close()

    try:
        asyncio.run(write())
    except Exception as exc:
        console.print(f"[red]not changed:[/] the change could not be audited ({exc}).")
        console.print("Start the database with `docker compose up -d db` and try again.")
        raise typer.Exit(1) from exc


@policy_app.command("show")
def policy_show() -> None:
    """Show active approval policy configuration."""
    _, policy_file = _policy_file()
    cfg = _read_policy(policy_file)
    if policy_file.is_file():
        console.print(f"Policy loaded from {policy_file}")
    else:
        console.print("Using compiled default policy (no policy.toml)")
    console.print(f"  R0 auto-allow: {cfg.r0_auto}")
    console.print(f"  R1 auto-allow: {cfg.r1_auto}")
    console.print(f"  R2 require approval: {cfg.r2_requires_approval}")
    console.print("  R3, R4: always require approval")
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
    """Tighten policy for a tool (require approval or deny). Takes effect on next start."""
    settings, policy_file = _policy_file()
    decision = PolicyDecision.DENY if deny else PolicyDecision.REQUIRE_APPROVAL
    try:
        updated = _read_policy(policy_file).with_override(tool, decision)
    except PolicyError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(2) from exc
    _record_policy_change(settings, tool, "tighten", decision.value)
    save_policy(policy_file, updated)
    console.print(f"Updated policy for '{tool}' -> {decision.value} in {policy_file}")


@policy_app.command("reset")
def policy_reset(
    tool: Annotated[str, typer.Argument(help="Tool name to reset override for.")],
) -> None:
    """Remove a tool's policy override. Takes effect on next start."""
    settings, policy_file = _policy_file()
    cfg = _read_policy(policy_file)
    if all(o.tool != tool for o in cfg.overrides):
        console.print(f"No override found for '{tool}'.")
        return
    _record_policy_change(settings, tool, "reset", "removed override")
    save_policy(policy_file, cfg.without_override(tool))
    console.print(f"Reset policy for '{tool}' in {policy_file}")


# ---- ops auth --------------------------------------------------------------------------------
Capabilities = Annotated[
    list[str],
    typer.Option("--capability", "-c", help="Permission to enable. Repeat for several."),
]


async def _with_auth(settings: Settings, work: Callable[[AuthKit], Awaitable[None]]) -> None:
    pool = await open_pool(settings.database_url, max_size=2)
    kit = build_auth(settings, pool, SystemClock())
    try:
        await work(kit)
    finally:
        await kit.http.aclose()
        await pool.close()


def _run_auth(work: Callable[[AuthKit], Awaitable[None]]) -> None:
    try:
        asyncio.run(_with_auth(load_settings(), work))
    except ConfigError as exc:
        console.print(f"[red]configuration error:[/] {exc}")
        raise typer.Exit(2) from exc
    except AuthError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    except Exception as exc:
        console.print(f"[red]auth failed:[/] {type(exc).__name__}. Is the database up?")
        raise typer.Exit(1) from exc


def _connect(provider: str, capabilities: list[str], setting: str) -> None:
    if not capabilities:
        known = ", ".join(CAPABILITIES[provider])
        console.print(f"[red]Choose what to enable[/] with --capability. Available: {known}.")
        raise typer.Exit(2)
    try:
        wanted = scopes_for(provider, capabilities)
    except KeyError as exc:
        console.print(f"[red]{exc.args[0]}[/]")
        raise typer.Exit(2) from exc

    async def work(kit: AuthKit) -> None:
        client = kit.clients.get(provider)
        if client is None:
            console.print(f"[red]{provider} is not configured.[/] Set {setting} in .env.")
            raise typer.Exit(2)
        earlier = {
            s for st in await kit.service.status() if st.provider == provider for s in st.scopes
        }
        grant = await sign_in(
            client,
            tuple(dict.fromkeys([*wanted, *sorted(earlier)])),  # keep what was already granted
            open_url=webbrowser.open,
            say=console.print,
        )
        state = await kit.service.connect(provider, grant)
        console.print(f"[green]Connected[/] {provider} as {state.account_id}.")

    _run_auth(work)


@auth_app.command("google")
def auth_google(capability: Capabilities = []) -> None:  # noqa: B006 - typer reads the default
    """Sign in to Google (Gmail, Calendar). Run again with more capabilities to add them."""
    _connect("google", capability, "OPS_GOOGLE_CLIENT_ID")


@auth_app.command("microsoft")
def auth_microsoft(capability: Capabilities = []) -> None:  # noqa: B006
    """Sign in to Microsoft (Teams). Work or school accounts only."""
    _connect("microsoft", capability, "OPS_MS_CLIENT_ID")


@auth_app.command("status")
def auth_status() -> None:
    """Show each account's state. Never prints a token."""

    async def work(kit: AuthKit) -> None:
        rows = await kit.service.status()
        for row in rows:
            scopes = ", ".join(s.rsplit("/", 1)[-1] for s in row.scopes) or "-"
            console.print(f"{row.provider:10} {row.account_id:32} {row.status.value:13} {scopes}")
        if not rows:
            console.print("No accounts connected. Run `ops auth google` or `ops auth microsoft`.")
        has_key = kit.secrets.get(GEMINI_API_KEY) is not None
        console.print(f"gemini     {'key stored in Keychain' if has_key else 'no key in Keychain'}")

    _run_auth(work)


@auth_app.command("revoke")
def auth_revoke(
    provider: Annotated[str, typer.Argument(help="google or microsoft")],
    account: Annotated[
        str | None, typer.Option(help="Account; default: every connected one.")
    ] = None,
) -> None:
    """Disconnect: revoke at the provider where possible, delete the Keychain entry."""

    async def work(kit: AuthKit) -> None:
        if provider not in CAPABILITIES:
            console.print(f"[red]Unknown provider {provider!r}.[/] Use google or microsoft.")
            raise typer.Exit(2)
        accounts = (
            [account]
            if account
            else [s.account_id for s in await kit.service.status() if s.provider == provider]
        )
        if not accounts:
            console.print(f"Nothing connected for {provider}.")
        for name in accounts:
            remote = await kit.service.revoke(provider, name)
            how = "revoked at the provider and removed" if remote else "removed locally"
            console.print(f"{provider} {name}: {how}.")

    _run_auth(work)


@auth_app.command("langsmith")
def auth_langsmith() -> None:
    """Store the LangSmith API key in the Keychain. Turn tracing on with OPS_LANGSMITH_TRACING."""
    key = typer.prompt("LangSmith API key", hide_input=True).strip()
    if not key:
        console.print("[red]No key entered.[/]")
        raise typer.Exit(2)
    KeychainSecretStore().set(LANGSMITH_API_KEY, key)
    console.print("Stored in the Keychain. Set OPS_LANGSMITH_TRACING=true to send traces.")


@auth_app.command("gemini")
def auth_gemini() -> None:
    """Store the Gemini API key in the Keychain. The key is typed hidden, never echoed."""
    key = typer.prompt("Gemini API key", hide_input=True).strip()
    if not key:
        console.print("[red]No key entered.[/]")
        raise typer.Exit(2)
    KeychainSecretStore().set(GEMINI_API_KEY, key)
    console.print("Stored in the Keychain.")
