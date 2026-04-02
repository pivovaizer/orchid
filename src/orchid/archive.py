import logging
import shutil
import subprocess
from pathlib import Path

from orchid.config import ArchivingConfig

log = logging.getLogger("orchid")


def find_completed_plots(dst_dirs: list[str]) -> list[Path]:
    """Scan dst directories for completed .plot and .bin files."""
    plots = []
    for dst in dst_dirs:
        dst_path = Path(dst)
        if not dst_path.exists():
            continue
        for f in dst_path.iterdir():
            if f.suffix in (".plot", ".plot2", ".bin") and f.is_file():
                plots.append(f)
    return sorted(plots, key=lambda p: p.stat().st_mtime)


def get_archive_dir_stats(archive_dirs: list[str]) -> dict[str, dict]:
    """Get disk stats for each archive directory."""
    result = {}
    for d in archive_dirs:
        path = Path(d)
        if not path.exists():
            continue
        usage = shutil.disk_usage(path)
        result[d] = {
            "free": usage.free,
            "total": usage.total,
            "used_pct": (usage.used / usage.total) * 100 if usage.total > 0 else 100,
        }
    return result


def list_plots_in_dir(archive_dir: str) -> set[str]:
    """List plot filenames already present in an archive directory."""
    path = Path(archive_dir)
    if not path.exists():
        return set()
    return {f.name for f in path.iterdir() if f.suffix in (".plot", ".plot2", ".bin") and f.is_file()}


def is_remote_dir(d: str) -> bool:
    """Check if directory is remote (user@host:/path format)."""
    return ":" in d and "@" in d


def select_archive_dir(
    cfg: ArchivingConfig,
    plot_path: Path,
    busy_dirs: set[str] | None = None,
) -> str | None:
    """Pick the best archive dir: no duplicates, not busy, most free space."""
    busy = busy_dirs or set()

    candidates = []
    for d in cfg.archive_dirs:
        # Skip dirs currently busy with another transfer
        if d in busy:
            log.debug("Skipping %s: transfer in progress", d)
            continue

        if is_remote_dir(d):
            # Remote dirs — can't check space/duplicates locally, just use them
            candidates.append(d)
        else:
            # Local dirs — check space and duplicates
            stats = get_archive_dir_stats([d])
            if d not in stats:
                continue
            info = stats[d]
            plot_size = plot_path.stat().st_size
            if info["free"] < (plot_size + cfg.min_free_bytes):
                log.debug("Skipping %s: not enough space", d)
                continue
            existing = list_plots_in_dir(d)
            if plot_path.name in existing:
                log.debug("Skipping %s: plot already exists", d)
                continue
            candidates.append(d)

    if not candidates:
        return None

    chosen = candidates[0]
    log.debug("Selected archive dir %s", chosen)
    return chosen


def _windows_to_wsl_path(path: Path) -> str:
    """Convert Windows path to WSL path: C:\\Users\\... -> /mnt/c/Users/..."""
    s = str(path).replace("\\", "/")
    if len(s) >= 2 and s[1] == ":":
        drive = s[0].lower()
        return f"/mnt/{drive}{s[2:]}"
    return s


def transfer_plot(cfg: ArchivingConfig, plot_path: Path, archive_dir: str, on_output=None) -> subprocess.Popen:
    """Start rsync/scp transfer of a plot to archive directory."""
    cmd_parts = cfg.command.split()
    source = str(plot_path)

    if "scp" in cfg.command.lower():
        # scp syntax: scp source user@host:/path/
        cmd = cmd_parts + [source, archive_dir + "/"]
    elif "wsl" in cfg.command.lower():
        # WSL rsync needs Linux-style paths
        flags = cfg.rsync_flags.split() if cfg.rsync_flags else []
        wsl_source = _windows_to_wsl_path(plot_path)
        cmd = cmd_parts + flags + [wsl_source, archive_dir + "/"]
    else:
        # Native rsync
        flags = cfg.rsync_flags.split() if cfg.rsync_flags else []
        cmd = cmd_parts + flags + [source, archive_dir + "/"]

    log.info("Transferring %s -> %s", plot_path.name, archive_dir)
    log.debug("Transfer command: %s", " ".join(cmd))

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return process


class Archiver:
    def __init__(self, cfg: ArchivingConfig, dst_dirs: list[str], on_log=None):
        self.cfg = cfg
        self.dst_dirs = dst_dirs
        self.on_log = on_log  # callback(msg) for TUI logging
        self._transfers: dict[str, tuple[subprocess.Popen, Path]] = {}  # dir -> (process, plot)

    def _busy_dirs(self) -> set[str]:
        """Return set of archive dirs currently receiving a transfer."""
        return set(self._transfers.keys())

    def _log(self, msg: str):
        log.info(msg)
        if self.on_log:
            self.on_log(msg)

    def _check_transfers(self) -> None:
        """Check and clean up completed transfers."""
        done = []
        for archive_dir, (proc, plot) in self._transfers.items():
            if proc.poll() is not None:
                if proc.returncode == 0:
                    self._log(f"Transfer complete: {plot.name}")
                    if "scp" in self.cfg.command.lower() and plot.exists():
                        plot.unlink()
                        log.info("Deleted source after scp: %s", plot.name)
                else:
                    self._log(f"Transfer failed (exit {proc.returncode}): {plot.name}")
                done.append(archive_dir)

        for d in done:
            del self._transfers[d]

    def has_pending(self, dst_dirs: list[str]) -> bool:
        """Check if there are plots to transfer or transfers in progress."""
        if self._transfers:
            return True
        return len(find_completed_plots(dst_dirs)) > 0

    def tick(self) -> None:
        """Called periodically by scheduler. Starts transfer if possible."""
        if not self.cfg.enabled:
            return

        self._check_transfers()

        # Only one transfer at a time (network bottleneck)
        if self._transfers:
            return

        plots = find_completed_plots(self.dst_dirs)
        if not plots:
            return

        plot = plots[0]  # oldest first
        archive_dir = select_archive_dir(self.cfg, plot, self._busy_dirs())
        if archive_dir is None:
            log.warning("No suitable archive directory for %s", plot.name)
            return

        size_mb = plot.stat().st_size / (1024 * 1024)
        self._log(f"Transferring {plot.name} ({size_mb:.0f} MB) -> {archive_dir}")
        proc = transfer_plot(self.cfg, plot, archive_dir)
        self._transfers[archive_dir] = (proc, plot)
