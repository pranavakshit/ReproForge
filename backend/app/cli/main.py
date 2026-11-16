"""ReproForge Command Line Interface."""

import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Optional
import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from backend.app.artifacts.collector import ArtifactCollector
from backend.app.core.config import settings
from backend.app.execution.runner import DockerBuildBackend, LocalSandboxBackend
from backend.app.execution.workspace import WorkspaceManager
from backend.app.fingerprinting.engine import fingerprinter
from backend.app.orchestration.detector import BuildConfigurationDetector
from backend.app.orchestration.git import GitRepositoryManager

console = Console()


@click.group()
@click.version_option(version=settings.APP_VERSION, prog_name=settings.APP_NAME)
def cli():
    """ReproForge: Cross-Environment Build Reproducibility & Diagnostic Framework."""
    pass


@cli.command("info")
def info_command():
    """Display system status, versions, and runtime capabilities."""
    sys_info = fingerprinter.capture_system_info()
    tools = fingerprinter.capture_toolchain_info()

    console.print(
        Panel.fit(
            f"[bold cyan]ReproForge[/bold cyan] v{settings.APP_VERSION}\n"
            f"[dim]Cross-Environment Software Build Reproducibility Framework[/dim]\n\n"
            f"[bold]Host OS:[/bold] {sys_info.os_name} {sys_info.os_release} ({sys_info.cpu_arch})\n"
            f"[bold]CPU Cores:[/bold] {sys_info.cpu_count} | [bold]Memory:[/bold] {round(sys_info.total_ram_bytes / (1024**3), 2)} GB\n"
            f"[bold]Node ID:[/bold] {sys_info.hostname_masked}\n"
            f"[bold]Workspaces Dir:[/bold] {settings.WORKSPACES_DIR}\n"
            f"[bold]Artifacts Dir:[/bold] {settings.ARTIFACTS_DIR}",
            title="System Telemetry",
            border_style="cyan",
        )
    )

    table = Table(title="Detected Build Toolchains", border_style="dim")
    table.add_column("Tool", style="bold")
    table.add_column("Version", style="green")

    for tool_name, details in tools.tools.items():
        ver = details.get("version", "available")
        table.add_row(tool_name, str(ver))

    console.print(table)


@cli.command("fingerprint")
@click.option("--json-out", is_flag=True, help="Output canonical fingerprint JSON.")
def fingerprint_command(json_out: bool):
    """Capture and display structured environment fingerprint and cryptographic digest."""
    fp = fingerprinter.capture_fingerprint()

    if json_out:
        console.print_json(data=fp.model_dump(mode="json"))
        return

    console.print(
        Panel(
            f"[bold green]Canonical Environment Digest (SHA-256):[/bold green]\n"
            f"[bold white]{fp.fingerprint_hash}[/bold white]",
            border_style="green",
        )
    )

    table = Table(title="Captured Environment State", border_style="dim")
    table.add_column("Dimension", style="bold cyan")
    table.add_column("Summary", style="white")

    table.add_row("Operating System", f"{fp.system_info.os_name} {fp.system_info.os_release} ({fp.system_info.cpu_arch})")
    table.add_row("Kernel Release", fp.system_info.kernel_release)
    table.add_row("Locale & TZ", f"LANG={fp.locale_info.lang or 'unset'}, TZ={fp.locale_info.timezone}, umask={fp.locale_info.umask}")
    table.add_row("Active Toolchains", ", ".join(fp.toolchain_info.tools.keys()))
    table.add_row("Sanitized Env Vars", f"{len(fp.environment_variables)} variables captured (secrets redacted)")
    table.add_row("Container Runtime", f"Available: {fp.container_runtime_info.get('available', False)}")

    console.print(table)


@cli.command("detect")
@click.argument("repo_path", type=click.Path(exists=True, file_okay=False, path_type=Path))
def detect_command(repo_path: Path):
    """Inspect repository directory and detect build configuration and dependencies."""
    detector = BuildConfigurationDetector()
    results = detector.detect(repo_path)

    table = Table(title=f"Detected Build Systems in {repo_path.name}", border_style="cyan")
    table.add_column("System Type", style="bold green")
    table.add_column("Primary Config", style="white")
    table.add_column("Lockfile", style="yellow")
    table.add_column("Dependencies", style="magenta")
    table.add_column("Suggested Build Command", style="cyan")

    for item in results:
        lock_status = f"Yes ({item.lockfile_path})" if item.has_lockfile else "No"
        deps = f"{item.declared_dependencies_count} declared" if item.declared_dependencies_count else "N/A"
        table.add_row(
            item.system_type,
            item.primary_config_file,
            lock_status,
            deps,
            item.suggested_build_command,
        )

    console.print(table)


@cli.command("build")
@click.option("--repo", "repo_path", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path), help="Path to Git repository.")
@click.option("--commit", "commit_ref", default="HEAD", help="Git commit SHA, branch, or tag (defaults to HEAD).")
@click.option("--backend", "backend_type", type=click.Choice(["local", "docker"]), default="local", help="Execution backend.")
@click.option("--command", "custom_cmd", default=None, help="Override build command.")
@click.option("--timeout", "timeout_seconds", default=1200, type=int, help="Execution timeout in seconds.")
def build_command(
    repo_path: Path,
    commit_ref: str,
    backend_type: str,
    custom_cmd: Optional[str],
    timeout_seconds: int,
):
    """Execute an isolated build of a specific Git revision and collect artifacts."""
    build_id = f"build-{uuid.uuid4().hex[:10]}"
    console.print(f"[bold cyan]ReproForge Build Orchestrator[/bold cyan] (ID: [bold white]{build_id}[/bold white])")

    # 1. Ingest Git revision
    git_mgr = GitRepositoryManager()
    resolved_sha = git_mgr.resolve_commit_sha(repo_path, commit_ref)
    console.print(f"Git Reference: [yellow]{commit_ref}[/yellow] -> Pinned SHA: [green]{resolved_sha}[/green]")

    # 2. Detect build configuration
    detector = BuildConfigurationDetector()
    systems = detector.detect(repo_path)
    primary_system = systems[0]
    build_cmd = custom_cmd or primary_system.suggested_build_command
    console.print(f"Target Build Command: [bold white]{build_cmd}[/bold white]")

    # 3. Setup workspace and export clean source revision
    ws_mgr = WorkspaceManager()
    ws_path = ws_mgr.allocate_workspace(build_id)
    source_dir = ws_path / "source"

    try:
        git_mgr.export_clean_revision(repo_path, source_dir, resolved_sha)
    except Exception as e:
        console.print(f"[red]Failed to export source revision: {e}[/red]")
        ws_mgr.cleanup_workspace(ws_path)
        sys.exit(1)

    # 4. Execute build
    console.print(f"Executing build via backend: [bold cyan]{backend_type}[/bold cyan]...")
    runner = DockerBuildBackend() if backend_type == "docker" else LocalSandboxBackend()

    async def run_build():
        return await runner.execute(
            build_id=build_id,
            workspace_source_dir=source_dir,
            build_command=build_cmd,
            timeout_seconds=timeout_seconds,
        )

    result = asyncio.run(run_build())

    # 5. Check outcome and collect artifacts
    if result.exit_code == 0:
        console.print(f"[bold green]Build Succeeded[/bold green] (Duration: {result.duration_seconds}s)")
        collector = ArtifactCollector()
        docker_tag = result.metadata.get("image_tag") if backend_type == "docker" else None
        artifacts = collector.collect_from_workspace(build_id, source_dir, docker_image_tag=docker_tag)

        if artifacts:
            table = Table(title="Collected Build Artifacts", border_style="green")
            table.add_column("Artifact File", style="bold")
            table.add_column("Type", style="cyan")
            table.add_column("Size (Bytes)", style="yellow")
            table.add_column("Strict SHA-256", style="dim white")

            for a in artifacts:
                table.add_row(a.relative_path, a.artifact_type, str(a.file_size_bytes), a.strict_sha256[:20] + "...")
            console.print(table)
        else:
            console.print("[dim yellow]Build completed but no output artifacts were detected in standard locations.[/dim yellow]")
    else:
        console.print(f"[bold red]Build Failed[/bold red] (Exit Code: {result.exit_code}, Duration: {result.duration_seconds}s)")
        console.print(f"Log output available at: {result.log_file_path}")


if __name__ == "__main__":
    cli()
