import logging
import signal
import time

from orchid.archive import Archiver
from orchid.disk import has_enough_space, estimate_plot_size, validate_dirs
from orchid.manager import PlotManager

log = logging.getLogger("orchid")


POLL_INTERVAL = 3  # seconds between status checks


class Scheduler:
    def __init__(self, manager: PlotManager):
        self.manager = manager
        self.running = True
        self.draining = False
        self._last_job_started: float = 0.0
        self.archiver = Archiver(
            cfg=manager.config.archiving,
            dst_dirs=manager.config.directories.dst,
        )
        # Track which dirs are currently healthy
        self._healthy_tmp: list[str] = []
        self._healthy_dst: list[str] = []
        self._prev_tmp: set[str] = set()
        self._prev_dst: set[str] = set()

    def handle_signal(self, signum, frame):
        if self.draining:
            log.warning("Force stopping all jobs...")
            self.manager.stop_all()
            self.running = False
        else:
            log.info("Draining... waiting for active jobs to finish. Ctrl+C again to force stop.")
            self.draining = True

    def _refresh_dirs(self) -> None:
        """Re-validate directories and log changes."""
        cfg = self.manager.config
        self._healthy_tmp, self._healthy_dst = validate_dirs(
            cfg.directories.tmp, cfg.directories.dst
        )

        # Detect dirs that came back online
        new_tmp = set(self._healthy_tmp)
        new_dst = set(self._healthy_dst)

        for d in new_tmp - self._prev_tmp:
            if self._prev_tmp:  # don't log on first run
                log.info("Tmp dir back online: %s", d)
        for d in self._prev_tmp - new_tmp:
            log.warning("Tmp dir went offline: %s", d)
        for d in new_dst - self._prev_dst:
            if self._prev_dst:
                log.info("Dst dir back online: %s", d)
        for d in self._prev_dst - new_dst:
            log.warning("Dst dir went offline: %s", d)

        self._prev_tmp = new_tmp
        self._prev_dst = new_dst

    def _pick_dirs(self) -> tuple[str, str] | None:
        """Pick tmp and dst dir with enough space."""
        k = self.manager.config.plotter.k
        needed = estimate_plot_size(k)

        tmp_dir = None
        for d in self._healthy_tmp:
            if has_enough_space(d, needed):
                tmp_dir = d
                break

        dst_dir = None
        for d in self._healthy_dst:
            if has_enough_space(d, needed):
                dst_dir = d
                break

        if tmp_dir is None:
            log.info("No tmp dir with enough space")
            return None
        if dst_dir is None:
            log.info("No dst dir with enough space")
            return None

        return tmp_dir, dst_dir

    def run(self):
        signal.signal(signal.SIGINT, self.handle_signal)

        # Initial validation
        self._refresh_dirs()
        if not self._healthy_tmp:
            log.error("No valid tmp directories found! Check config.")
        if not self._healthy_dst:
            log.error("No valid dst directories found! Check config.")

        log.info("Scheduler started (max_jobs=%d, stagger=%dm, archiving=%s, tmp=%d/%d, dst=%d/%d)",
                 self.manager.config.scheduler.max_jobs,
                 self.manager.config.scheduler.stagger_minutes,
                 "on" if self.manager.config.archiving.enabled else "off",
                 len(self._healthy_tmp), len(self.manager.config.directories.tmp),
                 len(self._healthy_dst), len(self.manager.config.directories.dst))

        stagger_secs = self.manager.config.scheduler.stagger_minutes * 60

        while self.running:
            self._refresh_dirs()
            self.manager.check_jobs()
            active = self.manager.get_active_jobs()

            if self.draining and not active:
                log.info("All jobs finished. Exiting.")
                break

            if not self.draining:
                now = time.time()
                stagger_ok = (now - self._last_job_started) >= stagger_secs
                if len(active) < self.manager.config.scheduler.max_jobs and stagger_ok:
                    dirs = self._pick_dirs()
                    if dirs:
                        tmp_dir, dst_dir = dirs
                        job = self.manager.create_job(tmp_dir=tmp_dir, dst_dir=dst_dir)
                        self.manager.start_job(job)
                        self._last_job_started = time.time()
                    else:
                        log.info("Waiting for disk space...")

            # Archive completed plots
            self.archiver.tick()

            time.sleep(POLL_INTERVAL)
