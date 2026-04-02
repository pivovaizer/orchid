import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("orchid")


def get_free_bytes(path: str) -> int | None:
    """Get free bytes on the disk where path is located."""
    p = Path(path)
    if not p.exists():
        return None
    return shutil.disk_usage(p).free


def get_total_bytes(path: str) -> int | None:
    """Get total bytes on the disk where path is located."""
    p = Path(path)
    if not p.exists():
        return None
    return shutil.disk_usage(p).total


def format_bytes(b: int) -> str:
    """Format bytes to human-readable string."""
    if b >= 1024 ** 4:
        return f"{b / 1024 ** 4:.1f} TB"
    if b >= 1024 ** 3:
        return f"{b / 1024 ** 3:.1f} GB"
    if b >= 1024 ** 2:
        return f"{b / 1024 ** 2:.1f} MB"
    return f"{b / 1024:.1f} KB"


def has_enough_space(path: str, required_bytes: int) -> bool:
    """Check if path has at least required_bytes free."""
    free = get_free_bytes(path)
    if free is None:
        return False
    return free >= required_bytes


def check_dir(path: str) -> bool:
    """Check if directory exists and is writable."""
    p = Path(path)
    if not p.exists():
        return False
    if not p.is_dir():
        return False
    try:
        test_file = p / ".orchid_write_test"
        test_file.touch()
        test_file.unlink()
        return True
    except OSError:
        return False


def validate_dirs(tmp_dirs: list[str], dst_dirs: list[str]) -> tuple[list[str], list[str]]:
    """Validate directories. Return lists of healthy dirs."""
    healthy_tmp = []
    for d in tmp_dirs:
        if check_dir(d):
            healthy_tmp.append(d)
        else:
            log.warning("Tmp dir unavailable: %s", d)

    healthy_dst = []
    for d in dst_dirs:
        if check_dir(d):
            healthy_dst.append(d)
        else:
            log.warning("Dst dir unavailable: %s", d)

    return healthy_tmp, healthy_dst


def parse_remote_dir(archive_dir: str) -> tuple[str, str] | None:
    """Parse 'user@host:/path' into (host, path). Returns None if not remote."""
    if "@" in archive_dir and ":" in archive_dir:
        host, path = archive_dir.split(":", 1)
        return host, path
    return None


def get_remote_disk_usage(host: str, path: str, timeout: int = 5) -> tuple[int, int] | None:
    """Get (free_bytes, total_bytes) from remote host via SSH df.

    Returns None if SSH fails or times out.
    """
    try:
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={timeout}", host, "df", "-B1", path],
            capture_output=True, text=True, timeout=timeout + 2,
        )
        if result.returncode != 0:
            return None
        lines = result.stdout.strip().splitlines()
        if len(lines) < 2:
            return None
        parts = lines[1].split()
        total = int(parts[1])
        free = int(parts[3])
        return free, total
    except Exception:
        return None


# Approximate plot sizes in bytes (for pos2, k -> size)
PLOT_SIZE_ESTIMATE = {
    18: 100 * 1024 ** 2,       # ~100 MB
    20: 400 * 1024 ** 2,       # ~400 MB
    22: 1_600 * 1024 ** 2,     # ~1.6 GB
    24: 6 * 1024 ** 3,         # ~6 GB
    26: 25 * 1024 ** 3,        # ~25 GB
    28: 100 * 1024 ** 3,       # ~100 GB
    30: 400 * 1024 ** 3,       # ~400 GB
    32: 1_600 * 1024 ** 3,     # ~1.6 TB (placeholder)
}


def estimate_plot_size(k: int) -> int:
    """Estimate plot file size for given k."""
    return PLOT_SIZE_ESTIMATE.get(k, 100 * 1024 ** 3)
