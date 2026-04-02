import logging

import click
from datetime import datetime
from pathlib import Path

from .loader import load_config
from .log import setup_logging, get_job_log_path
from .manager import PlotManager
from .scheduler import Scheduler
from .disk import get_free_bytes, get_total_bytes, format_bytes
from .job import JobStatus
from tui.app import OrchidApp


@click.group()
@click.option("--config", "-c", default="config.yaml", help="Path to configuration file")
@click.pass_context
def cli(ctx, config):
    ctx.ensure_object(dict)
    ctx.obj["config_error"] = None
    try:
        ctx.obj["config"] = load_config(config)
    except Exception as e:
        ctx.obj["config"] = None
        ctx.obj["config_error"] = str(e)
    ctx.obj["logger"] = setup_logging()


@cli.command()
@click.pass_context
def start(ctx):
    cfg = ctx.obj["config"]
    manager = PlotManager(cfg)
    scheduler = Scheduler(manager)
    click.echo("Orchid started. Ctrl+C to drain, double Ctrl+C to force stop.")
    scheduler.run()


@cli.command()
@click.pass_context
def status(ctx):
    cfg = ctx.obj["config"]
    manager = PlotManager(cfg)
    manager.check_jobs()

    active = [j for j in manager.jobs if j.status == JobStatus.RUNNING]
    completed = [j for j in manager.jobs if j.status == JobStatus.COMPLETED]
    failed = [j for j in manager.jobs if j.status == JobStatus.FAILED]

    click.echo(f"Jobs: {len(active)} running, {len(completed)} completed, {len(failed)} failed\n")

    if active:
        click.echo("=== Active jobs ===")
        click.echo(f"{'ID':<10} {'k':<4} {'str':<4} {'pid':<8} {'elapsed':<10} {'tmp':<20} {'dst'}")
        click.echo("-" * 80)
        for job in active:
            elapsed = "—"
            if job.start_time:
                delta = datetime.now() - job.start_time
                hours, remainder = divmod(int(delta.total_seconds()), 3600)
                minutes, _ = divmod(remainder, 60)
                elapsed = f"{hours}h{minutes:02d}m"
            click.echo(f"{job.job_id:<10} {job.k:<4} {job.strength:<4} {job.pid or '—':<8} {elapsed:<10} {job.tmp_dir:<20} {job.dst_dir}")

    if not active:
        click.echo("No active jobs.")


@cli.command()
@click.pass_context
def dirs(ctx):
    cfg = ctx.obj["config"]

    click.echo("=== Tmp directories ===")
    for d in cfg.directories.tmp:
        free = get_free_bytes(d)
        total = get_total_bytes(d)
        if free is not None and total is not None:
            used_pct = (1 - free / total) * 100
            click.echo(f"  {d}: {format_bytes(free)} free / {format_bytes(total)} total ({used_pct:.0f}% used)")
        else:
            click.echo(f"  {d}: NOT FOUND")

    click.echo("\n=== Dst directories ===")
    for d in cfg.directories.dst:
        free = get_free_bytes(d)
        total = get_total_bytes(d)
        if free is not None and total is not None:
            used_pct = (1 - free / total) * 100
            plots = list(Path(d).glob("*.plot")) + list(Path(d).glob("*.plot2")) + list(Path(d).glob("*.bin"))
            click.echo(f"  {d}: {format_bytes(free)} free / {format_bytes(total)} total ({used_pct:.0f}% used) [{len(plots)} plots]")
        else:
            click.echo(f"  {d}: NOT FOUND")

    if cfg.archiving.enabled and cfg.archiving.archive_dirs:
        click.echo("\n=== Archive directories ===")
        for d in cfg.archiving.archive_dirs:
            free = get_free_bytes(d)
            if free is not None:
                click.echo(f"  {d}: {format_bytes(free)} free")
            else:
                click.echo(f"  {d}: REMOTE/UNAVAILABLE")


@cli.command()
@click.argument("job_id")
def logs(job_id):
    log_path = get_job_log_path(job_id)
    if not log_path.exists():
        click.echo(f"No log found for job {job_id}")
        return
    click.echo(log_path.read_text())


@cli.command()
@click.argument("job_id")
@click.pass_context
def kill(ctx, job_id):
    cfg = ctx.obj["config"]
    manager = PlotManager(cfg)
    for job in manager.jobs:
        if job.job_id.startswith(job_id) and job.status == JobStatus.RUNNING:
            manager.stop_job(job)
            click.echo(f"Killed job {job.job_id}")
            return
    click.echo(f"No running job found matching '{job_id}'")


@cli.command()
@click.argument("job_id")
@click.pass_context
def details(ctx, job_id):
    cfg = ctx.obj["config"]
    manager = PlotManager(cfg)
    for job in manager.jobs:
        if job.job_id.startswith(job_id):
            click.echo(f"Job ID:      {job.job_id}")
            click.echo(f"Status:      {job.status.value}")
            click.echo(f"k:           {job.k}")
            click.echo(f"Strength:    {job.strength}")
            click.echo(f"Plot ID:     {job.plot_id}")
            click.echo(f"PID:         {job.pid or '—'}")
            click.echo(f"Tmp dir:     {job.tmp_dir}")
            click.echo(f"Dst dir:     {job.dst_dir}")
            click.echo(f"Started:     {job.start_time or '—'}")
            click.echo(f"Finished:    {job.finished_at or '—'}")
            if job.start_time:
                end = job.finished_at or datetime.now()
                delta = end - job.start_time
                hours, remainder = divmod(int(delta.total_seconds()), 3600)
                minutes, secs = divmod(remainder, 60)
                click.echo(f"Duration:    {hours}h{minutes:02d}m{secs:02d}s")
            if job.error_message:
                click.echo(f"Error:       {job.error_message}")
            return
    click.echo(f"No job found matching '{job_id}'")


@cli.command()
@click.option("--verbose", is_flag=True, default=False, help="Show full plotter output in logs")
@click.pass_context
def tui(ctx, verbose):
    # Disable console logging for TUI mode
    logger = logging.getLogger("orchid")
    for h in logger.handlers[:]:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            logger.removeHandler(h)

    from tui.app import OrchidApp
    cfg = ctx.obj["config"]
    config_error = ctx.obj.get("config_error")
    config_path = ctx.parent.params.get("config", "config.yaml")
    app = OrchidApp(config=cfg, config_path=config_path, verbose_logs=verbose, config_error=config_error)
    app.run()
