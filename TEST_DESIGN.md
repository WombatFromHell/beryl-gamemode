# Test Dependency Graph & Module Map

## Test Dependency Graph

```
tests/conftest.py                    (central fixtures & factories)
  ├── FakeRunner                     (canned subprocess responses)
  ├── feature_builder               (factory: FakeRunner + feature instantiation)
  ├── tmp_path_cfg                  (Config with all toggles off, state in tmp_path)
  ├── logger                        (deterministic logger with NullHandler)
  ├── runner                        (real Runner for integration tests)
  ├── niri_session                  (monkeypatched niri environment)
  ├── state_manager                 (pre-initialised StateManager)
  └── held_lock                     (file lock fixture for concurrency tests)

tests/test_cli.py                    (CLI parser — 2 tests)
  └── depends on: gamemode.cli_parse
       └── tested against: conftest.py (no fixture dependencies)

tests/test_config.py                 (Config loading — 5 tests)
  ├── depends on: gamemode.Config, gamemode._env_bool
  └── tested against: conftest.py (_cfg helper)

tests/test_feature.py                (FeatureResult protocol — 1 test class)
  ├── depends on: gamemode.FeatureResult
  └── tested against: conftest.py (no fixture dependencies)

tests/test_runner.py                 (Runner abstraction — 2 test classes)
  ├── depends on: gamemode.Runner, gamemode.CheckedCommandRunner
  └── tested against: conftest.py (logger, fake_runner fixtures)

tests/test_compositor.py             (Compositor detection — 2 test classes)
  ├── depends on: gamemode.compositor_is_niri(), session_is_kde(), output_resolve()
  └── tested against: conftest.py (tmp_path_cfg fixture)

tests/test_dependencies.py           (Dependency validation — 1 test class)
  ├── depends on: gamemode.validate_deps()
  └── tested against: conftest.py (_cfg, logger)
       └── uses: _FakeRunner (inline stub of Runner)

tests/test_orchestration.py          (Feature collection — 1 test class)
  ├── depends on: gamemode.collect_features()
  └── tested against: conftest.py (tmp_path_cfg, logger)
       └── tests: tuned, vrr, scx, audio, inhibit features collected

tests/test_logging.py                (Logging setup — 1 test class)
  ├── depends on: gamemode.setup_logging()
  └── tested against: conftest.py (tmp_path_cfg fixture)

tests/test_features.py               (Feature implementations — 7 test classes)
  ├── TestVRR                        (7 tests: enable/disable/skip/capable)
  ├── TestPowerProfile               (4 tests: enable/disable/noop/skip)
  ├── TestSCXScheduler               (6 tests: enable/disable/switch/noop/skip)
  ├── TestAudioPriority              (6 tests: enable/disable/env/file)
  ├── TestScreenInhibit              (10 tests: DMS/ScreenSaver/cookie/idempotent/error)
  ├── TestSteamWrapperPath           (3 tests: enabled/disabled/missing)
  └── tested against: conftest.py (feature_builder, niri_session, _cfg, _cp, _resolve)
       └── helper functions duplicated from conftest for module independence:
           _vrr_maps, _inhibit_maps, _dbus_uninhibit_cmd, _cfg, _cp, _resolve

tests/test_actions.py                (Action handlers — 4 test classes)
  ├── TestActionWrapper              (5 tests: cleanup/signal/concurrent/exitcode/oserror)
  ├── TestWatchParent                (3 tests: prctl success/fail/no_libc)
  ├── TestStateManagerLockLifetime   (1 test: lock held during child)
  └── tested against: conftest.py (tmp_path, logger, held_lock, _cfg)
       └── integration tests spawn real child processes via subprocess.Popen
```

## Test Module Map

### Test Configuration & Shared Infrastructure

| File          | Purpose                                                   | Key Fixtures                                                                                                       |
| ------------- | --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `conftest.py` | Central fixture definitions, FakeRunner, helper factories | `tmp_path_cfg`, `logger`, `runner`, `fake_runner`, `feature_builder`, `niri_session`, `state_manager`, `held_lock` |

### Unit Tests (by module)

| Test File               | Source Module      | Coverage                                                         | Test Count |
| ----------------------- | ------------------ | ---------------------------------------------------------------- | ---------- |
| `test_cli.py`           | `cli.py`           | `cli_parse()` — all argument modes                               | 10         |
| `test_config.py`        | `config.py`        | `Config` env vars, bool parsing, state_dir derivation            | 6          |
| `test_feature.py`       | `feature.py`       | `FeatureResult` factory methods (skip/did_change/error)          | 3          |
| `test_runner.py`        | `runner.py`        | `Runner.resolve()`, `require()`, `run()`, `CheckedCommandRunner` | 7          |
| `test_compositor.py`    | `compositor.py`    | niri/KDE detection, output resolution                            | 5          |
| `test_dependencies.py`  | `dependencies.py`  | `validate_deps()` — all feature combinations                     | 2          |
| `test_orchestration.py` | `orchestration.py` | `collect_features()` — all enabled features                      | 1          |
| `test_logging.py`       | `logging_setup.py` | console handler, file handler                                    | 2          |

### Integration Tests

| Test File          | Source Module | Coverage                                                                                                  | Test Count |
| ------------------ | ------------- | --------------------------------------------------------------------------------------------------------- | ---------- |
| `test_features.py` | `features.py` | All feature implementations: VRR, PowerProfile, SCXScheduler, AudioPriority, ScreenInhibit, Steam wrapper | 42         |
| `test_actions.py`  | `actions.py`  | `action_wrapper()` signal handling, cleanup, lock contention, parent-death detection                      | 9          |

### Test Coverage Summary

| Category    | Files | Tests  | Scope                                       |
| ----------- | ----- | ------ | ------------------------------------------- |
| Unit        | 7     | 36     | Individual module functions/classes         |
| Integration | 2     | 51     | Cross-module: features, actions, subprocess |
| **Total**   | **9** | **87** | All public API paths                        |

### Feature Test Matrix

| Feature         | Toggle Test            | Wrapper Test | Key Scenarios                                                       |
| --------------- | ---------------------- | ------------ | ------------------------------------------------------------------- |
| VRR             | ✓ (`test_features.py`) |              | enable/disable/already_on/already_off/skip_not_capable/skip_no_niri |
| PowerProfile    | ✓                      |              | enable/disable/already_game/noop/skip                               |
| SCXScheduler    | ✓                      |              | enable/disable/switch_scheduler/noop/skip                           |
| AudioPriority   | ✓                      |              | enable env/set file/disable clear/remove file                       |
| ScreenInhibit   | ✓                      |              | DMS/ScreenSaver fallback/cookie/idempotent/error/all_fail           |
| Steam wrapper   |                        | ✓            | enabled/missing_script/disabled                                     |
| inhibit wrapper |                        |              | (tested indirectly via ScreenInhibit integration)                   |

### Fixture Dependency Chain

```
conftest.py
├── _cfg()                    → builds Config with all toggles off
├── _cp()                     → CompletedProcess factory
├── _resolve()                → single-entry resolve map
├── FakeRunner                → canned subprocess responses
├── _make_feature()           → FakeRunner + feature instantiation
├── _vrr_maps()               → VRR test scenario maps
├── _inhibit_maps()           → ScreenInhibit test scenario maps
├── _dbus_uninhibit_cmd()     → ScreenSaver.UnInhibit command builder
├── tmp_path_cfg              → Config(tmp_path)
├── logger                    → gamemode.test with NullHandler
├── runner                    → real Runner(logger)
├── fake_runner               → FakeRunner(logger)
├── feature_builder           → factory for features with canned responses
├── niri_session              → monkeypatched niri environment
├── state_manager             → initialised StateManager
└── held_lock                 → file lock for concurrency testing
```
