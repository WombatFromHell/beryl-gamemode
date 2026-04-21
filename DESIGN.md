# Design Document

## Architecture Overview

Gamemode is a gaming performance toggle tool for Linux desktops, targeting the **niri** compositor and **KDE** sessions. It provides two modes of operation:

1. **Toggle mode** (`on`/`off`/`status`): Immediately enables or disables a set of system features
2. **Wrapper mode** (`-- <command>` or bare command): Spawns a child process with feature wrappers pre-pended, with auto-cleanup via signal guards and parent-death detection

## Dependency Graph

```
entry.py                          (application entry point)
  └── cli.py                     (CLI parser & main dispatcher)
       ├── __version__           (version resolution)
       ├── actions              (on/off/status/wrapper implementations)
       │    ├── compositor      (compositor detection: niri vs KDE)
       │    │    └── config     (configuration dataclass)
       │    ├── config
       │    ├── feature         (Feature protocol & base class)
       │    │    ├── config
       │    │    └── runner     (subprocess abstraction)
       │    ├── features        (all feature implementations + wrapper factories)
       │    │    ├── compositor
       │    │    ├── config
       │    │    ├── feature
       │    │    └── runner
       │    ├── logging_setup   (logger configuration)
       │    │    └── config
       │    ├── orchestration   (feature collection & enable/disable)
       │    │    ├── config
       │    │    ├── feature
       │    │    ├── features
       │    │    └── runner
       │    ├── runner
       │    └── state           (JSON state + file locking)
       │         └── config
       ├── config
       ├── dependencies         (command availability validation)
       │    ├── config
       │    └── runner
       ├── logging_setup
       └── runner

gamemode/__init__.py             (package root — re-exports public API)
gamemode/__version__.py          (version string with runtime fallback)
```

## Module Map

### Entry Point

| Module     | Purpose                                  |
| ---------- | ---------------------------------------- |
| `entry.py` | Imports and calls `main()` from `cli.py` |

### CLI Layer

| Module   | Purpose                                                                      |
| -------- | ---------------------------------------------------------------------------- |
| `cli.py` | Parses `on`/`off`/`status`/`wrapper` commands; dispatches to action handlers |

### Actions

| Module       | Purpose                                                                                                                                                     |
| ------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `actions.py` | Implements `on` (activate features), `off` (deactivate + clear state), `status` (diagnostic output), `wrapper` (launch child with wrappers + signal guards) |

### Configuration

| Module      | Purpose                                                                                                                                                                                     |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `config.py` | Loads `~/.config/gamemode.conf` (KEY=VALUE), provides frozen `Config` dataclass reading from env vars. Controls which features are enabled via `toggle_features` / `wrapper_features` sets. |

### State Management

| Module     | Purpose                                                                                                                                                  |
| ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `state.py` | `StateManager` — persists mode (active/wrapper), PID, and command to JSON file. Uses `fcntl.flock` for mutual exclusion (prevents concurrent instances). |

### Compositor Detection

| Module          | Purpose                                                                                                                              |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `compositor.py` | Detects **niri** via `XDG_SESSION_DESKTOP` or `pgrep -x niri`. Checks for **KDE** via env vars. Resolves target display output name. |

### Dependency Validation

| Module            | Purpose                                                                                                                                                          |
| ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `dependencies.py` | `validate_deps()` — checks for required commands (`tuned-adm`, `systemd-inhibit`, `dbus-send`, `scxctl`, `jq`) only when their corresponding feature is enabled. |

### Feature Protocol

| Module       | Purpose                                                                                                                                                                                                    |
| ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `feature.py` | Defines `Feature` protocol (requires `enable()`/`disable()` returning `FeatureResult`). Provides `_BaseFeature` with gating (`_gate`), guarded execution (`_guarded`), and result logging (`_log_result`). |

### Feature Implementations

| Module        | Purpose                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `features.py` | **Toggle features** (inherit `_BaseFeature`): `VRR` (niri VRR toggle via `niri msg`), `PowerProfile` (switches tuned profile via `tuned-adm`), `SCXScheduler` (starts/stops SCX scheduler via `scxctl`), `AudioPriority` (sets `PULSE_LATENCY_MSEC` env var), `ScreenInhibit` (prevents screen lock via DMS or DBus). **Wrapper factories**: `steam_wrapper_factory`, `inhibit_wrapper_factory`, `systemd_run_wrapper_factory`. **`WrapperChain`** — chains command wrappers sequentially. **`SystemdRun`** — prepends `systemd-run` to command argv (not a toggle feature). |

### Orchestration

| Module             | Purpose                                                                                                                                                        |
| ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `orchestration.py` | `collect_features()` — instantiates enabled features from config. `features_enable()`/`features_disable()` — iterate features and call `enable()`/`disable()`. |

### Runner Abstraction

| Module      | Purpose                                                                                                                                                                                                              |
| ----------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `runner.py` | `Runner` — wraps `subprocess.run` with logging. Supports `run()`, `capture()`, `pipe()`, and command existence checks via `require()`. `CheckedCommandRunner` — pre-validates command availability before execution. |

### Logging

| Module             | Purpose                                                                               |
| ------------------ | ------------------------------------------------------------------------------------- |
| `logging_setup.py` | Configures `gamemode` logger with console (stderr) handler and optional file handler. |

### Package Root

| Module        | Purpose                                                                                                                                                                   |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `__init__.py` | Re-exports entire public API: `Config`, `Runner`, all feature classes, wrapper factories, orchestration functions, action functions, CLI functions, and state management. |

## Data Flow

```
cli.main()
  → load_config_file() → Config()
  → validate_deps()
  → action_on() / action_off() / action_status() / action_wrapper()
    → _prepare_action()
      → collect_features() → list[Feature]
      → state.init() → state.locked()
    → features_enable() / features_disable()
      → Feature.enable(output) / Feature.disable(output)
        → Runner.run() → subprocess.run()
```

## Features

| Feature       | Toggle | Wrapper | Description                                                                          |
| ------------- | ------ | ------- | ------------------------------------------------------------------------------------ |
| `vrr`         | ✓      |         | Toggles VRR on a specific display output via niri IPC                                |
| `scx`         | ✓      |         | Starts/stops the SCX scheduler (default: `lavd` in `gaming` mode)                    |
| `tuned`       | ✓      |         | Switches system power profile via tuned daemon                                       |
| `audio`       | ✓      |         | Sets `PULSE_LATENCY_MSEC` for PulseAudio low-latency mode                            |
| `inhibit`     | ✓      | ✓       | Prevents screen blanking/lock via DMS (niri) or DBus; wrapper adds `systemd-inhibit` |
| `steam`       |        | ✓       | Pre-pends Steam environment script to command                                        |
| `systemd_run` |        | ✓       | Wraps command with `systemd-run` for resource control (CPU/IO weight)                |

## External Dependencies (Standard Library Only)

All dependencies are Python stdlib: `ctypes`, `dataclasses`, `fcntl`, `functools`, `importlib.metadata`, `json`, `logging`, `os`, `pathlib`, `shutil`, `signal`, `subprocess`, `sys`, `textwrap`, `typing`, `contextlib`.

No third-party packages required.

## Build System

The project uses a Makefile to produce a **deterministic, reproducible** Python zipapp (`.pyz`). Key build features:

- Fixed `SOURCE_DATE_EPOCH` (Jan 1, 1980) for reproducible timestamps
- `LC_ALL=C sort` for deterministic file ordering in the archive
- Stripped extra file attributes (`-X`)
- Version injected via `sed` into `__version__.py`
- SHA256 checksum generated for verification
