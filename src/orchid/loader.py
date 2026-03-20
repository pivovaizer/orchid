import yaml
from pathlib import Path
from .config import OrchidConfig


def load_config(path: str) -> OrchidConfig:
    config_path = Path(path)
    if not config_path.exists():
        return OrchidConfig()  # дефолтный конфиг
    
    with open(config_path) as f:
        data = yaml.safe_load(f)
    
    return OrchidConfig(**data)
