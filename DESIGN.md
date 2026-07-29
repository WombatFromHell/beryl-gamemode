# Design Document

## Architecture Overview

Gamemode is a gaming performance toggle tool for Linux desktops, targeting the **niri** compositor and **KDE** sessions. It provides two modes of operation:

1. **Toggle mode** (`on`/`off`/`status`): Immediately enables or disables a set of system features
2. **Wrapper mode** (`-- <command>` or bare command): Spawns a child process with feature wrappers pre-pended, with auto-cleanup via signal guards and parent-death detection

## Module Dependency Graph

All edges represent direct `import`/`from ... import` relationships.

```mermaid
graph LR
    subgraph entry["Entry Point"]
        entry_py[entry.py]
        version[__version__.py]
    end

    subgraph core["Core (leaf modules)"]
        config[config.py]
        runner[runner.py]
    end

    subgraph detection["Detection"]
        compositor[compositor.py]
        logging_setup[logging_setup.py]
    end

    subgraph protocol["Protocol"]
        feature[feature.py<br/>FeatureResult, Feature, _BaseFeature]
        dependencies[dependencies.py]
    end

    subgraph implementation["Implementation"]
        features_vrr[features/vrr.py]
        features_pp[features/power_profile.py]
        features_scx[features/scx_scheduler.py]
        features_audio[features/audio_priority.py]
        features_inhibit[features/screen_inhibit.py]
        features_wrappers[features/wrappers.py]
        state[state.py]
        orchestration[orchestration.py]
    end

    subgraph control["Control"]
        actions[actions.py]
        cli[cli.py]
    end

    entry_py --> cli
    cli --> version
    cli --> actions
    cli --> config
    cli --> dependencies
    cli --> logging_setup
    cli --> runner

    actions --> compositor
    actions --> config
    actions --> feature
    actions --> features_wrappers
    actions --> logging_setup
    actions --> orchestration
    actions --> runner
    actions --> state

    compositor --> config
    dependencies --> config
    dependencies --> runner
    feature --> config
    feature --> runner
    features_vrr --> compositor
    features_vrr --> config
    features_vrr --> feature
    features_vrr --> runner
    features_pp --> config
    features_pp --> feature
    features_pp --> runner
    features_scx --> config
    features_scx --> feature
    features_scx --> runner
    features_audio --> feature
    features_inhibit --> compositor
    features_inhibit --> config
    features_inhibit --> feature
    features_inhibit --> runner
    features_wrappers --> config
    features_wrappers --> feature
    features_wrappers --> runner
    logging_setup --> config
    orchestration --> config
    orchestration --> feature
    orchestration --> features_vrr
    orchestration --> features_pp
    orchestration --> features_scx
    orchestration --> features_audio
    orchestration --> features_inhibit
    orchestration --> runner
    state --> config
```

## Module Map

### Entry Point

| Module           | Purpose                                                              |
| ---------------- | -------------------------------------------------------------------- |
| `entry.py`       | Imports and calls `main()` from `cli.py`                             |
| `__version__.py` | Provides `__version__` and `_get_version()` via `importlib.metadata` |

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

| Module       | Purpose                                                                                                                                                                                                                                                                                              |
| ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `feature.py` | Defines `Feature` protocol, `CommandWrapper` and `WrapperFactory` type aliases. Provides `_BaseFeature` with gating (`_gate`), guarded execution (`_guarded`), result logging (`_log_result`), and `make_checked_cmd` helper for creating `CheckedCommandRunner` instances bound to the base runner. |

### Feature Implementations (Package)

| Module                       | Purpose                                                                                                                                                                                                                                                                                                                              |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `features/vrr.py`            | **VRR** — niri VRR toggle via `niri msg`; queries display capability via `jq`, toggles via `niri msg` IPC                                                                                                                                                                                                                            |
| `features/power_profile.py`  | **PowerProfile** — switches tuned profile via `tuned-adm`; reads current profile via `tuned-adm active`, sets profile via `tuned-adm profile`                                                                                                                                                                                        |
| `features/scx_scheduler.py`  | **SCXScheduler** — starts/stops SCX scheduler via `scxctl`; reads status via `scxctl status`, applies scheduler via `scxctl set-scheduler`                                                                                                                                                                                           |
| `features/audio_priority.py` | **AudioPriority** — sets `PULSE_LATENCY_MSEC` for PulseAudio low-latency mode; writes to env file and clears on disable                                                                                                                                                                                                              |
| `features/screen_inhibit.py` | **ScreenInhibit** — prevents screen lock via DMS (niri) or DBus (screensaver); checks display manager type via `compositor.py`. Includes evdev-based KB&M idle monitor (`_IdleMonitorThread`) that fires external commands on idle/active transitions independently of DMS.                                                          |
| `features/wrappers.py`       | **Wrapper factories**: `steam_wrapper_factory` (prepends Steam env script), `inhibit_wrapper_factory` (adds `systemd-inhibit`), `systemd_run_wrapper_factory` (prepends `systemd-run`). **`WrapperChain`** — chains command wrappers sequentially. **`SystemdRun`** — prepends `systemd-run` to command argv (not a toggle feature). |

### Orchestration

| Module             | Purpose                                                                                                                                                        |
| ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `orchestration.py` | `collect_features()` — instantiates enabled features from config. `features_enable()`/`features_disable()` — iterate features and call `enable()`/`disable()`. |

### Runner Abstraction

| Module      | Purpose                                                                                                                                                                                                                                                                |
| ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `runner.py` | `Runner` — wraps `subprocess.run` with logging. Supports `run()`, `capture()`, `pipe()`, `require()`, and `make_checked_runner()`. `CheckedCommandRunner` — pre-validates command availability before execution; provides `run_or_none()`, `run_ok()`, `is_available`. |

### Logging

| Module             | Purpose                                                                               |
| ------------------ | ------------------------------------------------------------------------------------- |
| `logging_setup.py` | Configures `gamemode` logger with console (stderr) handler and optional file handler. |

## Data Flow

### Shared Entry

```mermaid
graph LR
    A["cli.main()"] --> B[load_config_file]
    B --> C[Config]
    A --> D[setup_logging]
    A --> E[cli_parse]
    E --> F{mode}
    F -->|on| G[action_on]
    F -->|off| H[action_off]
    F -->|status| I[action_status]
    F -->|wrapper| J[action_wrapper]
    A --> K[validate_deps]
```

### Toggle Mode (on / off)

```mermaid
graph TD
    A[action_on / action_off] --> B[_prepare_action]
    B --> C[output_resolve]
    B --> D["StateManager.init"]
    B --> E[collect_features]
    E --> F["featureList[Feature]"]
    A --> G{state check}
    G -->|already active| H[return 0 idempotent]
    G -->|wrapper active| H
    G -->|fresh| I["state.mark_active"]
    I --> J[features_enable / features_disable]
    J --> K["Feature.enable / disable"]
    K --> L["Runner.run / capture / pipe"]
    L --> M["subprocess.run"]
    A --> N["state.clear"]
```

### Wrapper Mode

```mermaid
graph TD
    A[action_wrapper] --> B[output_resolve]
    A --> C["StateManager.init"]
    A --> D[_watch_parent<br/>prctl PR_SET_PDEATHSIG]
    A --> E{"state.locked"}
    E -->|lock held| F[skip — another wrapper active]
    E -->|acquired| G{already active?}
    G -->|yes| H[skip features — apply wrappers only]
    G -->|no| I["state.mark_wrapper"]
    I --> J[collect_features]
    J --> K[features_enable]
    K --> L["Feature.enable"]
    H --> M[build WrapperChain]
    L --> M
    M --> N[WRAPPER_FACTORIES<br/>steam, inhibit, systemd_run]
    N --> O[_run_child]
    O --> P["subprocess.Popen"]
    O --> Q[_signal_guard<br/>SIGTERM/SIGINT/SIGHUP]
    Q --> R["child.wait"]
    R --> S[cleanup closure]
    S --> T[features_disable]
    T --> U["state.clear if preserve_state=False"]
```

### Status Mode

```mermaid
graph LR
    A[action_status] --> B["StateManager.init"]
    B --> C[_build_status_lines]
    C --> D[compositor_is_niri]
    C --> E[session_is_kde]
    C --> F[output_resolve]
    C --> G["state.mode / pid / cmd"]
    D --> H[print diagnostics]
    E --> H
    F --> H
    G --> H
```

## Feature Interdependencies

Features depend on external commands, compositor state, and environment variables. Missing dependencies are handled gracefully (skip/noop) rather than failing.

```mermaid
graph TD
    subgraph features["Feature Implementations"]
        vrr["VRR"]
        pp["PowerProfile"]
        scx["SCXScheduler"]
        audio["AudioPriority"]
        inhibit["ScreenInhibit"]
    end

    subgraph external["External Commands"]
        niri["niri msg"]
        jq["jq"]
        tuned["tuned-adm"]
        scxctl["scxctl"]
        dms["dms ipc"]
        dbus["dbus-send"]
    end

    subgraph env["Environment / State"]
        niri_detect["compositor_is_niri()"]
        pulse["PULSE_LATENCY_MSEC"]
        audio_file["audio_env_file"]
    end

    vrr -->|queries outputs| niri
    vrr -->|parses JSON| jq
    vrr -->|toggle VRR| niri
    vrr -.->|requires niri running| niri_detect

    pp -->|active profile| tuned
    pp -->|switch profile| tuned

    scx -->|status/start/stop| scxctl

    audio -->|set/clear| pulse
    audio -->|write/remove| audio_file

    inhibit -->|niri only| dms
    inhibit -->|fallback always| dbus
    inhibit -.->|DMS path requires niri| niri_detect
```

### Feature Execution Rules

| Feature       | Gate             | Compositor requirement | External deps          | Fallback behavior                                                                                  |
| ------------- | ---------------- | ---------------------- | ---------------------- | -------------------------------------------------------------------------------------------------- |
| VRR           | `enable_vrr`     | niri only              | `niri`, `jq`           | Skip if not niri or not capable                                                                    |
| PowerProfile  | `enable_tuned`   | None                   | `tuned-adm`            | Noop if already on correct profile                                                                 |
| SCXScheduler  | `enable_scx`     | None                   | `scxctl`               | Noop if already loaded                                                                             |
| AudioPriority | `enable_audio`   | None                   | None (env + file only) | Always succeeds                                                                                    |
| ScreenInhibit | `enable_inhibit` | niri for DMS path      | `dms`, `dbus-send`     | Falls back to ScreenSaver if DMS fails; optional evdev idle monitor gated by `enable_idle_monitor` |

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

All dependencies are Python stdlib: `ctypes`, `dataclasses`, `fcntl`, `functools`, `importlib.metadata`, `json`, `logging`, `os`, `pathlib`, `select`, `shutil`, `signal`, `struct`, `subprocess`, `sys`, `textwrap`, `threading`, `time`, `typing`, `contextlib`.

No third-party packages required.

## Build System

The project uses a Makefile to produce a **deterministic, reproducible** Python zipapp (`.pyz`). Key build features:

- Fixed `SOURCE_DATE_EPOCH` (Jan 1, 1980) for reproducible timestamps
- `LC_ALL=C sort` for deterministic file ordering in the archive
- Stripped extra file attributes (`-X`)
- Version injected via `sed` into `__version__.py`
- SHA256 checksum generated for verification
