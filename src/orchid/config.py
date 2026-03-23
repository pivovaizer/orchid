from pydantic import BaseModel, Field, field_validator
from orchid.constants import K_MAX, K_MIN, STRENGTH_MAX, STRENGTH_MIN


class PlotterConfig(BaseModel):
    executable: str = "plotter"
    k: int = 28
    strength: int = 2
    plot_id: str = ""
    plot_index: int = 0
    meta_group: int = 0
    testnet: bool = True
    verbose: bool = True

    # Chia keys — will be required for mainnet
    farmer_key: str = ""
    pool_key: str = ""
    contract_address: str = ""

    @field_validator("k")
    @classmethod
    def validate_k(cls, v: int) -> int:
        if v < K_MIN or v > K_MAX or v % 2 != 0:
            raise ValueError(f"k must be an even integer between {K_MIN} and {K_MAX}")
        return v

    @field_validator("strength")
    @classmethod
    def validate_strength(cls, v: int) -> int:
        if v < STRENGTH_MIN or v > STRENGTH_MAX:
            raise ValueError(f"strength must be between {STRENGTH_MIN} and {STRENGTH_MAX}")
        return v

    def build_command(self, plot_id: str) -> list[str]:
        """Build the plotter command from config."""
        cmd = [
            self.executable,
            "test",
            str(self.k),
            plot_id,
            str(self.strength),
            str(self.plot_index),
            str(self.meta_group),
            "1" if self.verbose else "0",
        ]
        if self.testnet:
            cmd.append("--testnet")
        # Future: when pos2 supports keys
        # if self.farmer_key:
        #     cmd.extend(["--farmer-key", self.farmer_key])
        # if self.pool_key:
        #     cmd.extend(["--pool-key", self.pool_key])
        # if self.contract_address:
        #     cmd.extend(["--contract", self.contract_address])
        return cmd


class DirectoriesConfig(BaseModel):
    tmp: list[str] = ["tmp"]
    dst: list[str] = ["dst"]


class SchedulerConfig(BaseModel):
    max_jobs: int = 4
    max_plots: int = 0  # 0 = infinite
    stagger_minutes: int = 1
    use_gpu: bool = False


class ArchivingConfig(BaseModel):
    enabled: bool = False
    command: str = "rsync"
    rsync_flags: str = "-avP --remove-source-files"
    archive_dirs: list[str] = []
    min_free_bytes: int = 10_000_000_000  # 10GB minimum free on archive


class OrchidConfig(BaseModel):
    plotter: PlotterConfig = Field(default_factory=PlotterConfig)
    directories: DirectoriesConfig = Field(default_factory=DirectoriesConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    archiving: ArchivingConfig = Field(default_factory=ArchivingConfig)
