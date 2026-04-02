# Orchid

Plot manager for Chia PoS2 (Proof of Space v2). Automates plot creation, scheduling, and archiving to remote storage.

## Features

- **Automated plotting** -- continuously creates plots using the pos2 plotter binary
- **Job scheduling** -- parallel jobs with configurable stagger between launches
- **Smart disk management** -- validates directories, monitors free space, auto-skips unavailable drives and re-enables them when available
- **Archiving** -- transfers completed plots to remote storage via rsync/scp with automatic cleanup
- **Remote disk monitoring** -- TUI shows free space on remote archive machines via SSH
- **Even distribution** -- fills archive disks evenly, avoids duplicate plots and IO contention
- **Graceful shutdown** -- `Ctrl+C` to drain (finish current jobs), double `Ctrl+C` to force stop
- **State persistence** -- survives restarts, recovers running jobs
- **TUI dashboard** -- real-time monitoring with job progress, disk usage, and logs
- **Setup wizard** -- interactive first-run configuration

## Requirements

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/) package manager
- pos2 plotter binary (compiled from source or via chia)
- rsync or scp (for archiving to remote machines)

## Installation

```bash
git clone <repo-url>
cd orchid
uv sync
```

## Quick start

```bash
# Option 1: Interactive TUI (recommended)
uv run orchid tui
# First run will open a setup wizard to configure everything

# Option 2: CLI mode
cp config-example.yaml config.yaml
# Edit config.yaml with your settings
uv run orchid start
```

## Configuration

Copy `config-example.yaml` to `config.yaml` and edit:

```yaml
plotter:
  executable: "/path/to/plotter"    # path to pos2 plotter binary
  k: 28                              # plot size (18-32, even only)
  strength: 2                        # proof strength (2-255)
  testnet: true                      # testnet mode

directories:
  tmp:
    - "/mnt/nvme0/tmp"               # fast NVMe for temp files
  dst:
    - "/mnt/ssd/plots"               # completed plots land here

scheduler:
  max_jobs: 4                        # parallel plotting jobs
  max_plots: 0                       # total plots to create (0 = infinite)
  stagger_minutes: 30                # delay between job launches

archiving:
  enabled: true
  command: "rsync"
  rsync_flags: "-avP --remove-source-files"
  archive_dirs:
    - "farmer@192.168.1.10:/mnt/hdd01/plots"
    - "farmer@192.168.1.10:/mnt/hdd02/plots"
  min_free_bytes: 10000000000        # 10GB reserve per disk
```

## Usage

### TUI mode (recommended)

```bash
# Default — clean logs (job events only)
uv run orchid tui

# Verbose — full plotter output in logs
uv run orchid tui --verbose
```

| Keybinding | Action |
|---|---|
| `Ctrl+S` | Start plotting |
| `Ctrl+E` | Stop (drain / force stop) |
| `Ctrl+R` | Refresh disk info |
| `Ctrl+Q` | Quit |

The TUI shows three panels:
- **Jobs** -- active jobs with progress, PID, elapsed time
- **Disks** -- free space on local dirs and remote archive machines (via SSH)
- **Logs** -- job events and transfer status

### CLI mode

```bash
# Start plotting (foreground, Ctrl+C to stop)
uv run orchid start

# Start in background
nohup uv run orchid start &

# Check job status
uv run orchid status

# Show disk usage
uv run orchid dirs

# View plotter output for a specific job
uv run orchid logs <job_id>

# Job details
uv run orchid details <job_id>

# Kill a specific job
uv run orchid kill <job_id>
```

### Shutdown behavior (CLI mode)

| Action | Effect |
|---|---|
| `Ctrl+C` (once) | Stop launching new jobs, wait for active to finish |
| `Ctrl+C` (twice) | Kill all running jobs immediately |

## SSH key setup

Required for archiving and remote disk monitoring. Do this once on the plotting machine:

**Linux / macOS:**
```bash
ssh-keygen -t ed25519
ssh-copy-id farmer@192.168.1.10
```

**Windows (PowerShell):**
```powershell
ssh-keygen -t ed25519
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh farmer@192.168.1.10 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
```

Verify it works without a password prompt:
```bash
ssh farmer@192.168.1.10 df -h
```

After this, Orchid will automatically show remote disk usage in the TUI and transfer plots without any interaction.

## Deployment scenarios

### Linux to Linux (recommended)

The standard setup. Plotter runs on a fast machine, plots archive to a farmer via rsync.

```yaml
plotter:
  executable: "/usr/local/bin/plotter"

archiving:
  enabled: true
  command: "rsync"
  rsync_flags: "-avP --remove-source-files"
  archive_dirs:
    - "farmer@192.168.1.10:/mnt/farm/plots"
```

**Prerequisites:**
- SSH key authentication: `ssh-copy-id farmer@192.168.1.10`
- rsync installed on both machines

### Windows to Linux

Plotting on Windows, archiving to a Linux farmer via scp.

```yaml
plotter:
  executable: "C:/path/to/plotter.exe"

archiving:
  enabled: true
  command: "scp"
  rsync_flags: ""
  archive_dirs:
    - "farmer@192.168.1.10:/mnt/farm/plots"
```

**Prerequisites:**
- SSH key authentication:
  ```powershell
  ssh-keygen -t ed25519
  type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh farmer@192.168.1.10 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
  ```
- rsync on Windows — pick one:
  - **WSL** (recommended if already installed): `sudo apt install rsync`, use `command: "wsl rsync"` in config
  - **MSYS2**: `winget install MSYS2.MSYS2`, then in MSYS2 terminal: `pacman -S rsync`, use full path `command: "C:/msys64/usr/bin/rsync.exe"`

### Single machine (no archiving)

Plotting and farming on the same machine.

```yaml
plotter:
  executable: "/usr/local/bin/plotter"

directories:
  tmp:
    - "/mnt/nvme/tmp"
  dst:
    - "/mnt/hdd01/plots"
    - "/mnt/hdd02/plots"

archiving:
  enabled: false
```

### Testnet (development)

Quick setup for testing.

```yaml
plotter:
  executable: "/path/to/plotter"
  k: 28
  testnet: true

directories:
  tmp:
    - "/tmp/orchid"
  dst:
    - "/tmp/orchid"

scheduler:
  max_jobs: 1
  max_plots: 1
  stagger_minutes: 0

archiving:
  enabled: false
```

## Project structure

```
src/orchid/              # Core application
  cli.py                 # CLI commands (click)
  config.py              # Configuration models (pydantic)
  constants.py           # PoS2 constants
  loader.py              # YAML config loader
  job.py                 # Plot job model
  manager.py             # Job lifecycle management
  scheduler.py           # Main loop, job scheduling
  archive.py             # rsync/scp transfers, disk distribution
  disk.py                # Disk space checks, directory validation
  state.py               # JSON state persistence
  log.py                 # Logging setup

src/tui/                 # Terminal UI
  app.py                 # Main TUI application (textual)
  app.tcss               # TUI styles
  setup.py               # First-run setup wizard
```

## Status

Core features working and tested:
- Plot creation with correct plot_id via `chia_rs.compute_plot_id_v2`
- BLS key generation (taproot for NFT plots, OG plots)
- Memo injection + `.bin` → `.plot2` rename
- Real-time TUI dashboard
- Archiving via rsync/scp
- Disk monitoring and validation

Waiting for full PoS2 release. When pos2-chip CLI exposes `--farmer-key` / `--contract`, remove `inject_memo()` post-processing and update `config.py:build_command()`.

## License

MIT
