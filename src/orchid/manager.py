import logging
import secrets
import subprocess
import threading
import uuid
from datetime import datetime
from typing import Callable

import psutil

from orchid.config import OrchidConfig
from orchid.job import PlotJob, JobStatus
from orchid.state import StateStore

log = logging.getLogger("orchid")


class PlotManager:
    def __init__(self, config: OrchidConfig, on_output: Callable[[str, str], None] | None = None):
        self.config = config
        self._processes: dict[str, subprocess.Popen] = {}
        self._output_threads: dict[str, threading.Thread] = {}
        self.on_output = on_output  # callback(job_id, line)
        self.state_store = StateStore()
        self.jobs = self.state_store.load_state()
        self._recover_running_jobs()
        log.debug("PlotManager initialized, loaded %d jobs from state", len(self.jobs))

    def _recover_running_jobs(self) -> None:
        """Reconnect to running processes after orchid restart."""
        for job in self.jobs:
            if job.status != JobStatus.RUNNING or job.pid is None:
                continue
            try:
                proc = psutil.Process(job.pid)
                if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                    log.info("Recovered running job %s (pid %d)", job.job_id, job.pid)
                    continue
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            # Process is gone — mark as failed
            job.status = JobStatus.FAILED
            job.finished_at = datetime.now()
            job.error_message = "Process not found after restart"
            log.warning("Job %s (pid %d) not found, marking as failed", job.job_id, job.pid)
        self._save()

    def _save(self) -> None:
        self.state_store.save_state(self.jobs)

    def create_job(self, tmp_dir: str | None = None, dst_dir: str | None = None) -> PlotJob:
        plot_id = self.config.plotter.plot_id or secrets.token_hex(32)
        job = PlotJob(
            job_id=uuid.uuid4().hex[:8],
            k=self.config.plotter.k,
            strength=self.config.plotter.strength,
            plot_id=plot_id,
            plot_index=self.config.plotter.plot_index,
            meta_group=self.config.plotter.meta_group,
            tmp_dir=tmp_dir or self.config.directories.tmp[0],
            dst_dir=dst_dir or self.config.directories.dst[0],
        )
        self.jobs.append(job)
        self._save()
        log.info("Created job %s (k=%d, strength=%d)", job.job_id, job.k, job.strength)
        return job

    def start_job(self, job: PlotJob) -> None:
        cmd = self.config.plotter.build_command(job.plot_id)

        log.info("Starting job %s: %s", job.job_id, " ".join(cmd))

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=job.dst_dir,
            text=True,
        )
        job.pid = process.pid
        job.status = JobStatus.RUNNING
        job.start_time = datetime.now()
        self._processes[job.job_id] = process
        self._save()
        log.info("Job %s started with pid %d", job.job_id, process.pid)

        # Read plotter output in background thread
        t = threading.Thread(target=self._read_output, args=(job.job_id, process), daemon=True)
        t.start()
        self._output_threads[job.job_id] = t

    def _read_output(self, job_id: str, process: subprocess.Popen):
        """Read stdout from plotter process and forward to callback."""
        for line in process.stdout:
            line = line.rstrip()
            if line:
                log.debug("[%s] %s", job_id, line)
                if self.on_output:
                    self.on_output(job_id, line)

    def get_active_jobs(self) -> list[PlotJob]:
        return [j for j in self.jobs if j.status == JobStatus.RUNNING]

    def check_jobs(self) -> None:
        for job in self.get_active_jobs():
            process = self._processes.get(job.job_id)
            if process:
                # We have the Popen object
                if process.poll() is not None:
                    job.finished_at = datetime.now()
                    if process.returncode == 0:
                        job.status = JobStatus.COMPLETED
                        log.info("Job %s completed successfully", job.job_id)
                    else:
                        job.status = JobStatus.FAILED
                        job.error_message = f"Exit code: {process.returncode}"
                        log.error("Job %s failed with exit code %d", job.job_id, process.returncode)
            elif job.pid:
                # No Popen (after restart) — check via psutil
                try:
                    proc = psutil.Process(job.pid)
                    if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
                        job.status = JobStatus.COMPLETED
                        job.finished_at = datetime.now()
                        log.info("Job %s (pid %d) finished (detected via psutil)", job.job_id, job.pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    job.status = JobStatus.COMPLETED
                    job.finished_at = datetime.now()
                    log.info("Job %s (pid %d) finished (process gone)", job.job_id, job.pid)
        self._save()

    def stop_job(self, job: PlotJob) -> None:
        process = self._processes.get(job.job_id)
        if process:
            process.terminate()
            log.info("Terminated process %d for job %s", process.pid, job.job_id)
        elif job.pid:
            try:
                proc = psutil.Process(job.pid)
                proc.terminate()
                log.info("Terminated process %d for job %s (via psutil)", job.pid, job.job_id)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        job.status = JobStatus.FAILED
        job.finished_at = datetime.now()
        job.error_message = "Stopped by user"
        self._save()

    def stop_all(self) -> list[PlotJob]:
        active = self.get_active_jobs()
        for job in active:
            self.stop_job(job)
        log.info("Stopped %d jobs", len(active))
        return active

