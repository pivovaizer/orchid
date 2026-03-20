import re
import time
import threading
import shutil
from datetime import datetime

from textual.app import App, ComposeResult
from textual.widgets import Static, Header, Footer, DataTable, RichLog
from textual.containers import Vertical, Horizontal

from orchid.manager import PlotManager
from orchid.loader import load_config
from orchid.job import JobStatus
from orchid.archive import Archiver


class JobPanel(Static):
    """Active plotting jobs."""

    def compose(self) -> ComposeResult:
        yield Static("Jobs", classes="panel-title")
        yield DataTable()


class DiskPanel(Static):
    """Disk usage info."""

    def compose(self) -> ComposeResult:
        yield Static("Disks", classes="panel-title")
        yield DataTable()


class LogPanel(Static):
    """Log output."""

    def compose(self) -> ComposeResult:
        yield Static("Logs", classes="panel-title")
        yield RichLog(markup=True)


class OrchidApp(App):
    """Orchid Plot Manager TUI."""

    TITLE = "Orchid"
    SUB_TITLE = "Plot Manager"
    CSS_PATH = "app.tcss"

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+s", "start_plotting", "Start"),
        ("ctrl+e", "stop_plotting", "Stop"),
        ("ctrl+r", "refresh", "Refresh"),
    ]

    def __init__(self, config=None, config_path: str = "config.yaml"):
        super().__init__()
        self.config = config
        self.config_path = config_path
        self.manager = None
        self._plotting = False
        self._draining = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            JobPanel(),
            Horizontal(
                DiskPanel(),
                LogPanel(),
            ),
        )
        yield Footer()

    def on_mount(self):
        # Jobs table columns
        jobs_table = self.query_one(JobPanel).query_one(DataTable)
        jobs_table.add_columns("ID", "K", "Status", "PID", "Time", "Progress")

        # Disks table columns
        disk_table = self.query_one(DiskPanel).query_one(DataTable)
        disk_table.add_columns("Directory", "Free", "Total", "Used %")
        self.refresh_disks()
        self.set_interval(5, self.refresh_disks)

        # Show setup wizard if no config
        if not self.config or not self.config.plotter.executable or self.config.plotter.executable == "plotter":
            from tui.setup import SetupScreen
            self.push_screen(SetupScreen(self.config_path), callback=self._on_setup_done)
        else:
            self._show_welcome()

    def _on_setup_done(self, config_path: str | None):
        """Called when setup wizard finishes."""
        if config_path:
            self.config = load_config(config_path)
            self.refresh_disks()
            self.log_write("[green]Config saved![/green]")
        self._show_welcome()

    def _show_welcome(self):
        self.log_write("Orchid TUI started")
        if self.config:
            dirs = self.config.directories.tmp + self.config.directories.dst
            self.log_write(f"Monitoring {len(set(dirs))} directories")
            self.log_write("Press Ctrl+S to start plotting")

    # ── Logging ──────────────────────────────────────────────

    def log_write(self, msg: str):
        """Write a message to the log panel."""
        self.query_one(LogPanel).query_one(RichLog).write(msg)

    # ── Disks ────────────────────────────────────────────────

    def refresh_disks(self):
        table = self.query_one(DiskPanel).query_one(DataTable)
        table.clear()

        dirs = []
        if self.config:
            dirs = list(set(self.config.directories.tmp + self.config.directories.dst))
        for d in dirs:
            try:
                usage = shutil.disk_usage(d)
                free_gb = f"{usage.free / (1024**3):.1f} GB"
                total_gb = f"{usage.total / (1024**3):.1f} GB"
                used_pct = f"{usage.used / usage.total * 100:.1f}%"
                table.add_row(d, free_gb, total_gb, used_pct)
            except OSError:
                table.add_row(d, "N/A", "N/A", "N/A")

    # ── Jobs ─────────────────────────────────────────────────

    def refresh_jobs(self):
        """Update jobs table from manager state."""
        if not self.manager:
            return

        self.manager.check_jobs()
        table = self.query_one(JobPanel).query_one(DataTable)
        table.clear()

        for job in self.manager.jobs:
            elapsed = ""
            if job.start_time:
                end = job.finished_at if job.finished_at else datetime.now()
                delta = end - job.start_time
                minutes = int(delta.total_seconds() // 60)
                seconds = int(delta.total_seconds() % 60)
                elapsed = f"{minutes}m {seconds}s"

            # Build progress string: "45% matching T2"
            progress_str = ""
            if job.status == JobStatus.RUNNING:
                progress_str = f"{job.progress}% {job.phase}"
            elif job.status == JobStatus.COMPLETED:
                progress_str = "100% done"

            table.add_row(
                job.job_id[:8],
                str(job.k),
                job.status.value,
                str(job.pid or "-"),
                elapsed,
                progress_str,
            )

        # Check for completed jobs — log once
        for job in self.manager.jobs:
            if job.status == JobStatus.COMPLETED and not hasattr(job, '_logged'):
                self.log_write(f"[green]Job {job.job_id[:8]} completed[/green]")
                job._logged = True

    # ── Plotting control ─────────────────────────────────────

    # Regex to parse plotter progress lines like:
    # [====                        ] 13% matching T1 3.60073s
    _PROGRESS_RE = re.compile(r'\]\s*(\d+)%\s+(.+?)\s+[\d.]+s')

    def action_start_plotting(self):
        if self._plotting:
            self.log_write("[yellow]Already plotting[/yellow]")
            return

        self._plotting = True
        self._draining = False
        self.manager = PlotManager(self.config, on_output=self._on_plotter_output)
        self.manager.jobs.clear()
        self.manager._save()

        # Start archiver if enabled
        self.archiver = None
        if self.config.archiving.enabled:
            self.archiver = Archiver(
                self.config.archiving,
                self.config.directories.dst,
                on_log=lambda msg: self.call_from_thread(self.log_write, f"[cyan]{msg}[/cyan]"),
            )
            self.log_write("[green]Starting plotter (archiving ON)...[/green]")
        else:
            self.log_write("[green]Starting plotter...[/green]")

        self._plot_thread = threading.Thread(target=self._plot_loop, daemon=True)
        self._plot_thread.start()

        # Refresh jobs table every second for smooth progress updates
        self.set_interval(1, self.refresh_jobs, name="job_refresh")

    def _on_plotter_output(self, job_id: str, line: str):
        """Parse plotter output — update progress on job, log only important lines."""
        # Try to parse progress bar line
        match = self._PROGRESS_RE.search(line)
        if match:
            pct = int(match.group(1))
            phase = match.group(2)
            # Update job progress
            for job in self.manager.jobs:
                if job.job_id == job_id:
                    job.progress = pct
                    job.phase = phase
                    break
            return  # Don't log progress lines

        # Log important lines (not progress bars)
        if line.strip():
            self.call_from_thread(self.log_write, f"[dim]{job_id[:8]}[/dim] {line}")

    def _plot_loop(self):
        """Background thread: create and manage plot jobs."""
        plots_created = 0
        max_plots = self.config.scheduler.max_plots  # 0 = infinite

        try:
            while self._plotting:
                if self._draining:
                    active = [j for j in self.manager.jobs if j.status == JobStatus.RUNNING]
                    if not active:
                        self.call_from_thread(self.log_write, "[green]All jobs finished. Stopped.[/green]")
                        self._plotting = False
                        break
                    time.sleep(3)
                    continue

                self.manager.check_jobs()
                active = [j for j in self.manager.jobs if j.status == JobStatus.RUNNING]

                # Check if we hit the limit
                if max_plots > 0 and plots_created >= max_plots:
                    if not active:
                        # Wait for archiver to finish transfers
                        if self.archiver and self.archiver.has_pending(self.config.directories.dst):
                            if not hasattr(self, '_done_logged'):
                                self.call_from_thread(self.log_write, f"[green]All {max_plots} plots done. Waiting for transfers...[/green]")
                                self._done_logged = True
                            self.archiver.tick()
                            time.sleep(3)
                            continue
                        self.call_from_thread(self.log_write, f"[green]All {max_plots} plots done. All transfers complete.[/green]")
                        self._plotting = False
                        break
                    time.sleep(3)
                    continue

                if len(active) < self.config.scheduler.max_jobs:
                    job = self.manager.create_job()
                    if job:
                        self.manager.start_job(job)
                        plots_created += 1
                        self.call_from_thread(
                            self.log_write,
                            f"[green]Started job {job.job_id[:8]} (k={job.k}, pid={job.pid}) [{plots_created}/{max_plots or '∞'}][/green]",
                        )

                # Check archiver — transfer completed plots
                if self.archiver:
                    self.archiver.tick()

                time.sleep(3)
        except Exception as e:
            self.call_from_thread(self.log_write, f"[red]Error: {e}[/red]")

    def action_stop_plotting(self):
        if not self._plotting:
            self.log_write("[yellow]Nothing running[/yellow]")
            return

        if self._draining:
            self.log_write("[red]Force stopping all jobs...[/red]")
            if self.manager:
                self.manager.stop_all()
            self._plotting = False
            self._draining = False
            return

        self._draining = True
        self.log_write("[yellow]Draining... waiting for active jobs. Press Stop again to force.[/yellow]")

    def action_refresh(self):
        self.refresh_disks()
        self.refresh_jobs()
        self.log_write("[blue]Refreshed[/blue]")


def run_tui(config=None):
    app = OrchidApp(config=config)
    app.run()
