import logging
import re
import secrets
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

import psutil

from orchid.config import OrchidConfig
from orchid.job import PlotJob, JobStatus
from orchid.state import StateStore
from orchid.plot_keys import (
    generate_keys_and_plot_id,
    generate_plot_id_testnet,
    finalize_plot,
)

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
        cfg = self.config.plotter
        memo_hex = ""

        if cfg.plot_id:
            # Explicit plot_id from config
            plot_id = cfg.plot_id
        elif cfg.farmer_key and (cfg.pool_key or cfg.contract_address):
            # Generate proper plot_id from chia keys
            result = generate_keys_and_plot_id(
                k=cfg.k,
                strength=cfg.strength,
                plot_index=cfg.plot_index,
                meta_group=cfg.meta_group,
                farmer_pk_hex=cfg.farmer_key,
                pool_pk_hex=cfg.pool_key,
                contract_address_hex=cfg.contract_address,
            )
            if result:
                plot_id, memo, _master_sk = result
                memo_hex = memo.hex()
                log.info("Generated plot_id from chia keys")
            else:
                plot_id = generate_plot_id_testnet()
                log.info("BLS not available, using random plot_id (testnet)")
        else:
            # No keys — testnet random
            plot_id = generate_plot_id_testnet()

        job = PlotJob(
            job_id=uuid.uuid4().hex[:8],
            k=cfg.k,
            strength=cfg.strength,
            plot_id=plot_id,
            plot_index=cfg.plot_index,
            meta_group=cfg.meta_group,
            tmp_dir=tmp_dir or self.config.directories.tmp[0],
            dst_dir=dst_dir or self.config.directories.dst[0],
            memo_hex=memo_hex,
        )
        self.jobs.append(job)
        self._save()
        log.info("Created job %s (k=%d, strength=%d, plot_id=%s...)",
                 job.job_id, job.k, job.strength, plot_id[:16])
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

    # Regex patterns for pos2-chip verbose output
    _RE_PLOT_STARTED = re.compile(r"Plotting started")
    _RE_ALLOC_BEGIN = re.compile(r"Allocating memory")
    _RE_ALLOC_END = re.compile(r"Memory allocation completed.*Time:\s*([\d.]+)\s*ms")
    _RE_TABLE_BEGIN = re.compile(r"Constructing Table (\d+) from (\d+) items")
    _RE_TABLE_END = re.compile(r"Table (\d+) constructed.*Time:\s*([\d.]+)\s*ms")
    _RE_WRITING = re.compile(r"Writing plot to (.+)")
    _RE_WROTE = re.compile(r"Wrote plot file:\s*(.+?)\s*\((\d+) bytes\).*\[([\d.]+) bits/entry\].*in\s*([\d.]+)\s*ms")
    _RE_PLOT_ENDED = re.compile(r"Plotting ended.*Total time:\s*([\d.]+)\s*ms")
    _RE_PROGRESS_BAR = re.compile(r"\]\s*(\d+)%\s+(.+?)\s+[\d.]+s")

    # 3 tables in pos2: each ~30%, writing ~10%
    _TABLE_PROGRESS = {1: 0, 2: 30, 3: 60}

    def _read_output(self, job_id: str, process: subprocess.Popen):
        """Read stdout from plotter process, parse progress, forward to callback."""
        job = next((j for j in self.jobs if j.job_id == job_id), None)

        for line in process.stdout:
            line = line.rstrip()
            if not line:
                continue

            log.debug("[%s] %s", job_id, line)

            # Parse progress from verbose output
            if job:
                self._parse_progress(job, line)

            if self.on_output:
                self.on_output(job_id, line)

    def _parse_progress(self, job: PlotJob, line: str) -> None:
        """Update job progress/phase from plotter output line."""
        if self._RE_PLOT_STARTED.search(line):
            job.phase = "started"
            job.progress = 0
        elif self._RE_ALLOC_BEGIN.search(line):
            job.phase = "allocating"
            job.progress = 1
        elif self._RE_ALLOC_END.search(line):
            job.phase = "allocated"
            job.progress = 2
        elif m := self._RE_TABLE_BEGIN.search(line):
            table_num = int(m.group(1))
            job.phase = f"table_{table_num}"
            job.progress = self._TABLE_PROGRESS.get(table_num, 0) + 5
        elif m := self._RE_TABLE_END.search(line):
            table_num = int(m.group(1))
            job.phase = f"table_{table_num}_done"
            job.progress = self._TABLE_PROGRESS.get(table_num, 0) + 28
        elif self._RE_WRITING.search(line):
            job.phase = "writing"
            job.progress = 90
        elif m := self._RE_WROTE.search(line):
            job.phase = "done"
            job.progress = 100
        elif m := self._RE_PLOT_ENDED.search(line):
            job.phase = "done"
            job.progress = 100
        elif m := self._RE_PROGRESS_BAR.search(line):
            # Fallback: parse non-verbose progress bar
            job.progress = int(m.group(1))
            job.phase = m.group(2).strip()

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
                        self._finalize_plot(job)
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

    def _finalize_plot(self, job: PlotJob) -> None:
        """Post-process completed plot: inject memo + rename .bin → .plot2."""
        memo = bytes.fromhex(job.memo_hex) if job.memo_hex else None
        result = finalize_plot(
            plot_dir=Path(job.dst_dir),
            k=job.k,
            strength=job.strength,
            plot_index=job.plot_index,
            meta_group=job.meta_group,
            plot_id_hex=job.plot_id,
            testnet=self.config.plotter.testnet,
            memo=memo,
        )
        if result:
            job.plot_file = str(result)
            log.info("Job %s finalized: %s", job.job_id, result.name)
        else:
            log.warning("Job %s: could not finalize plot file", job.job_id)
        self._save()

    def stop_all(self) -> list[PlotJob]:
        active = self.get_active_jobs()
        for job in active:
            self.stop_job(job)
        log.info("Stopped %d jobs", len(active))
        return active

