import json
from pathlib import Path
from .config import OrchidConfig
from orchid.job import PlotJob, JobStatus


STATE_FILE = Path.home() / ".orchid" / "state.json"


class StateStore:
    def __init__(self, path: Path = STATE_FILE):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)


    def save_state(self, jobs: list[PlotJob]) -> None:
        data = [job.model_dump(mode="json") for job in jobs]
        self.path.write_text(json.dumps(data, indent=2))


    def load_state(self) -> list[PlotJob]:
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text())
        return [PlotJob.model_validate(d) for d in data]
