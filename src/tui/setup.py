import yaml
from pathlib import Path

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, Input, Button, Header, Footer
from textual.containers import Vertical, Center


class SetupScreen(Screen):
    """First-run setup wizard."""

    CSS = """
    SetupScreen {
        background: #1F262A;
    }

    #setup-container {
        width: 70;
        height: auto;
        border: round #334D5C;
        padding: 1 2;
        margin: 2 0;
    }

    .setup-title {
        text-style: bold;
        color: #66B2FF;
        text-align: center;
        width: 100%;
        height: 1;
        margin-bottom: 1;
    }

    .setup-label {
        color: #66B2FF;
        height: 1;
        margin-top: 1;
    }

    Input {
        margin-bottom: 0;
    }

    #save-btn {
        margin-top: 2;
        width: 100%;
    }
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(self, config_path: str = "config.yaml"):
        super().__init__()
        self.config_path = config_path

    def compose(self) -> ComposeResult:
        yield Header()
        yield Center(
            Vertical(
                Static("Orchid Setup", classes="setup-title"),
                Static("Path to plotter executable:", classes="setup-label"),
                Input(placeholder="C:/path/to/plotter.exe", id="executable"),
                Static("K size (18-32, even):", classes="setup-label"),
                Input(placeholder="28", id="k", value="28"),
                Static("Strength (2-255):", classes="setup-label"),
                Input(placeholder="2", id="strength", value="2"),
                Static("Tmp directory (for temp files):", classes="setup-label"),
                Input(placeholder="C:/plotting/tmp", id="tmp_dir"),
                Static("Dst directory (for completed plots):", classes="setup-label"),
                Input(placeholder="C:/plots", id="dst_dir"),
                Static("Farmer public key (optional):", classes="setup-label"),
                Input(placeholder="leave empty for testnet", id="farmer_key"),
                Static("Pool public key (optional):", classes="setup-label"),
                Input(placeholder="leave empty for testnet", id="pool_key"),
                Button("Save & Continue", id="save-btn", variant="success"),
                id="setup-container",
            ),
        )
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "save-btn":
            self._save_config()

    def _save_config(self):
        executable = self.query_one("#executable", Input).value.strip()
        k = self.query_one("#k", Input).value.strip()
        strength = self.query_one("#strength", Input).value.strip()
        tmp_dir = self.query_one("#tmp_dir", Input).value.strip()
        dst_dir = self.query_one("#dst_dir", Input).value.strip()
        farmer_key = self.query_one("#farmer_key", Input).value.strip()
        pool_key = self.query_one("#pool_key", Input).value.strip()

        if not executable or not tmp_dir or not dst_dir:
            self.notify("Please fill in executable, tmp and dst directories", severity="error")
            return

        config = {
            "plotter": {
                "executable": executable,
                "k": int(k) if k else 28,
                "strength": int(strength) if strength else 2,
                "testnet": not bool(farmer_key),
                "farmer_key": farmer_key,
                "pool_key": pool_key,
            },
            "directories": {
                "tmp": [tmp_dir],
                "dst": [dst_dir],
            },
            "scheduler": {
                "max_jobs": 1,
                "stagger_minutes": 0,
            },
            "archiving": {
                "enabled": False,
            },
        }

        with open(self.config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False)

        self.dismiss(self.config_path)

    def action_cancel(self):
        self.dismiss(None)
