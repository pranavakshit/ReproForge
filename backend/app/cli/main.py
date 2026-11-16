"""ReproForge Command Line Interface."""

import asyncio
from pathlib import Path
from typing import Optional
import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from backend.app.core.config import settings
from backend.app.orchestration.detector import BuildConfigurationDetector
from backend.app.schemas.entities import BuildStatus
from backend.app.services.build_service import BuildService
from backend.app.services.comparison_service import ComparisonService
from backend.app.services.fingerprint_service import FingerprintService

console = Console()


@click.group()
@click.version_option(version=settings.APP_VERSION, prog_name=settings.APP_NAME)
def cli():
    """ReproForge: Cross-Environment Build Reproducibility & Diagnostic Framework."""
    pass


@cli.command("info")
def info_command():
    """Display system status, versions, and runtime capabilities."""
    fp_svc = FingerprintService()
    sys_info, tools = fp_svc.get_telemetry()

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
    fp_svc = FingerprintService()
    fp = fp_svc.capture_fingerprint()

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
    build_svc = BuildService()

    console.print(f"[bold cyan]ReproForge Build Orchestrator[/bold cyan]")
    console.print(f"Git Reference: [yellow]{commit_ref}[/yellow]")
    console.print(f"Executing build via backend: [bold cyan]{backend_type}[/bold cyan]...")

    outcome = asyncio.run(
        build_svc.execute_build(
            repo_path=repo_path,
            commit_ref=commit_ref,
            backend_type=backend_type,
            custom_cmd=custom_cmd,
            timeout_seconds=timeout_seconds,
        )
    )

    if outcome.exit_code == 0:
        console.print(f"[bold green]Build Succeeded[/bold green] (Duration: {outcome.duration_seconds}s)")
        if outcome.artifacts:
            table = Table(title="Collected Build Artifacts", border_style="green")
            table.add_column("Artifact File", style="bold")
            table.add_column("Type", style="cyan")
            table.add_column("Size (Bytes)", style="yellow")
            table.add_column("Strict SHA-256", style="dim white")

            for a in outcome.artifacts:
                table.add_row(a.relative_path, a.artifact_type, str(a.file_size_bytes), a.strict_sha256[:20] + "...")
            console.print(table)
        else:
            console.print("[dim yellow]Build completed but no output artifacts were detected in standard locations.[/dim yellow]")
    else:
        console.print(f"[bold red]Build Failed[/bold red] (Exit Code: {outcome.exit_code}, Duration: {outcome.duration_seconds}s)")
        console.print(f"Log output available at: {outcome.log_file_path}")


@cli.command("compare")
@click.argument("artifact1", type=click.Path(exists=True, dir_okay=True, path_type=Path))
@click.argument("artifact2", type=click.Path(exists=True, dir_okay=True, path_type=Path))
def compare_command(artifact1: Path, artifact2: Path):
    """Compare two build artifacts in Strict (Mode A) and Normalized (Mode B) modes."""
    console.print(f"[bold cyan]ReproForge Artifact Differential Engine[/bold cyan]")
    console.print(f"Artifact 1: [yellow]{artifact1.name}[/yellow]")
    console.print(f"Artifact 2: [yellow]{artifact2.name}[/yellow]\n")

    comp_svc = ComparisonService()
    outcome = asyncio.run(comp_svc.compare_artifacts(artifact1, artifact2))

    summary = outcome.summary
    diagnostics = outcome.diagnostics
    remediations = outcome.remediations
    score = outcome.assessment

    exact_badge = "[bold green]MATCH[/bold green]" if summary.exact_match else "[bold red]DIVERGED[/bold red]"
    norm_badge = "[bold green]EQUIVALENT[/bold green]" if summary.normalized_match else "[bold red]DIVERGED[/bold red]"

    verdict_colors = {
        "bit_for_bit_reproducible": "bold green",
        "normalized_reproducible": "bold cyan",
        "partially_reproducible": "bold yellow",
        "non_reproducible": "bold red",
        "inconclusive": "bold magenta",
    }
    v_style = verdict_colors.get(score.verdict.value, "bold white")
    verdict_badge = f"[{v_style}]{score.verdict.value.upper()}[/{v_style}]"

    console.print(
        Panel(
            f"[bold]Primary Reproducibility Verdict:[/bold] {verdict_badge}\n"
            f"[bold]Mode A (Strict Bit-for-Bit):[/bold] {exact_badge}\n"
            f"  Artifact 1 SHA-256: [dim]{summary.strict_sha_1}[/dim]\n"
            f"  Artifact 2 SHA-256: [dim]{summary.strict_sha_2}[/dim]\n\n"
            f"[bold]Mode B (Normalized Equivalence):[/bold] {norm_badge}\n"
            f"  Normalized SHA-1:   [dim]{summary.normalized_sha_1}[/dim]\n"
            f"  Normalized SHA-2:   [dim]{summary.normalized_sha_2}[/dim]\n\n"
            f"[bold]Metric Vector:[/bold]\n"
            f"  • Content Agreement Ratio:  [cyan]{round(summary.content_agreement_ratio * 100, 2)}%[/cyan]\n"
            f"  • Metadata Agreement Ratio: [magenta]{round(summary.metadata_agreement_ratio * 100, 2)}%[/magenta]\n"
            f"  • Environment Consistency:  [yellow]{round(score.environment_consistency_score * 100, 2)}%[/yellow]\n"
            f"  • Unexplained Differences:  [white]{score.unexplained_differences_count}[/white]\n\n"
            f"[bold]Secondary Benchmark Scalar (CRI):[/bold] [bold white]{score.composite_reproducibility_index}[/bold white] / 1.0\n"
            f"[dim]({score.cri_disclaimer})[/dim]",
            title="Reproducibility Assessment",
            border_style="cyan",
        )
    )

    if summary.findings:
        table = Table(title="Detected Differences Breakdown", border_style="yellow")
        table.add_column("Relative Path", style="bold")
        table.add_column("Discrepancy Type", style="cyan")
        table.add_column("Strict", style="red")
        table.add_column("Normalized", style="green")
        table.add_column("Size Delta", style="yellow")

        for f in summary.findings[:25]:
            table.add_row(
                f.file_path,
                f.diff_type,
                "[red]Diverged[/red]" if not f.strict_match else "[green]Match[/green]",
                "[green]Equivalent[/green]" if f.normalized_match else "[red]Diverged[/red]",
                f"{f.size_delta_bytes:+d} B",
            )

        console.print(table)
        if len(summary.findings) > 25:
            console.print(f"[dim]... and {len(summary.findings) - 25} more differences.[/dim]")

    if diagnostics:
        diag_table = Table(title="Diagnostic Root-Cause Findings", border_style="red")
        diag_table.add_column("Category", style="bold red")
        diag_table.add_column("Confidence", style="bold yellow")
        diag_table.add_column("Root Cause Summary", style="white")

        for d in diagnostics:
            diag_table.add_row(
                d.category.value,
                d.confidence.value.upper(),
                d.root_cause_summary,
            )
        console.print(diag_table)

    if remediations:
        rem_table = Table(title="Actionable Remediation Guidance", border_style="green")
        rem_table.add_column("Action", style="bold green")
        rem_table.add_column("Risk Level", style="cyan")
        rem_table.add_column("Approval", style="yellow")
        rem_table.add_column("Recommended Action / Code", style="white")

        for r in remediations:
            snippet = f"\n[dim]{r.code_snippet}[/dim]" if r.code_snippet else ""
            rem_table.add_row(
                r.title,
                r.risk_level.value.upper(),
                "Required" if r.approval_required else "Autonomous (isolated)",
                f"{r.proposed_action}{snippet}",
            )
        console.print(rem_table)


if __name__ == "__main__":
    cli()
