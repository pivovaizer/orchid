from pydantic import BaseModel
from enum import Enum
from datetime import datetime


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class PlotJob(BaseModel):
    job_id: str
    k: int
    strength: int
    plot_id: str
    plot_index: int = 0
    meta_group: int = 0
    tmp_dir: str
    dst_dir: str
    status: JobStatus = JobStatus.PENDING
    pid: int | None = None
    start_time: datetime | None = None
    finished_at: datetime | None = None
    error_message: str | None = None
    progress: int = 0          # 0-100%
    phase: str = ""            # e.g. "matching T1", "postsort T2"
