# AURUS 3.0 — Final Implementation and Recovery Report

Date: 2026-11-04 (Asia/Colombo). Implementation and simulation tests only.

**Current behavior update, 2026-11-05:** Sections R/S supersede O/Q speech and
cache behavior. Ordinary speech has its previous Piper/Edge/eSpeak selection;
only the FIT Expo demo requests live Groq Orpheus/Troy narration for each line
on every run, with demo-only EQ/compression added in S. Saved demo recordings
have been removed. Section P's remaining
valid-sensor policy is unchanged: partial coverage is a warning and strict
coverage is opt-in. Earlier sections retain the history of prior revisions.
Section T adds the dashboard camera ON/OFF control, fresh-session checks and
camera-off exploration pause without changing speech or ultrasonic policy.

## A. Overview

This update strengthens local obstacle cancellation, adds structured MCP feedback,
uses the existing Groq/TTS loop to explain blocked movement, adds individual
ultrasonic controls and diagnostics, and implements calibrated time-based
distance requests. Changes extend the existing runtime rather than replacing it.

No local LLM, encoders, hardware dependencies, GPIO reassignment, dependency
installation, API-key changes, deployment, service restart, commit, or push was
performed. No real motors, sensors, camera, or microphone were operated.

The existing execution flow was inspected: `run.py` creates `RobotRuntime`,
which owns the sampler, MotionArbiter, behavior, camera, speech, TTS, SQLite
repository, and Groq MCP agent. Flask-SocketIO adapts that runtime. Voice and
text actions enter the Groq tool loop; movement tools submit through the arbiter.
The separate MCP server creates its own arbiter and must remain a separate,
exclusive hardware owner. Legacy `aurus_brain.py` remains unchanged.

## B. Git Recovery Baseline

| Item | Original value |
| --- | --- |
| Branch | `main` |
| Commit | `846b29c736eb6f6b820d31fb1d0c0ffb124bb537` |
| `git status --short` | Empty: clean tracked/untracked working tree |
| Pre-existing uncommitted modifications | None reported by Git |

Ignored runtime files, including `aurus_memory.db`, existed and were preserved.
No `AGENTS.md` was found in the repository. Git inspection and diff commands
were read-only; no branch, index, history, remote, commit, or push was changed.
All implementation changes remain uncommitted for review.

## C. Feature Status Table

| Feature | Implementation status | Remaining verification |
| --- | --- | --- |
| Obstacle stop and cancel | Fully implemented; blocked commands latch, mode becomes idle, no automatic resume/reroute | Requires hardware verification of stopping and latency |
| Structured MCP results | Fully implemented in runtime and standalone movement/spin/animation tools | Pi/actual MCP client integration |
| Groq obstacle explanation | Fully implemented in existing runtime tool loop; no extra dedicated explanation API call | Live Groq/network/model verification |
| TTS obstacle explanation | Fully implemented in runtime using existing TTS; one final explanation per objective | Speaker, playback routing and microphone feedback on Pi |
| Individual ultrasonic switches | Fully implemented with token authorization, invalidation, restore defaults and telemetry | Dashboard/browser and physical sensor verification |
| Sensor health | Fully implemented; per-sensor errors, age, health and coverage restrictions | Physical echo behavior, noise and sampling performance |
| Time-based distance | Fully implemented; deliberately unavailable until operator calibration exists | Requires measured calibration and hardware verification |
| Optional manual-dashboard spoken warning | Not implemented; blocked reason remains visible without adding warning speech | Optional feature only |

The standalone MCP server returns structured results to its client; it does not
run an additional Groq conversation itself. Its existing client is responsible
for explaining/speaking results. The built-in dashboard/voice MCP agent handles
Groq-to-TTS explanations automatically. Mock tests do not establish hardware success.

## D. Modified Files

| Relative path | Functions/classes changed | Purpose and existing behavior preserved |
| --- | --- | --- |
| `.env.example` | Configuration examples | Adds operator token and blank calibration settings; existing key fields unchanged |
| `pytest.ini` | Test discovery | Includes final safety tests; hardware voltage script stays excluded |
| `src/core/models.py` | New `SensorReading`; `SensorSnapshot`; `MotionDecision` | Adds per-sensor health/coverage and structured cancellation; retains legacy fields |
| `src/hardware/sensors.py` | `ProximitySensor._read_physical_sensor` | Monotonic echo timing; timeouts raise errors instead of fabricated 400 cm; pin assignments/filtering unchanged |
| `src/services/sensor_sampler.py` | `SensorSampler`, `sample_once`, `_publish`, `set_enabled`, `restore_defaults` | Individual polling, health, invalidation and configuration generation checks; preserves single sampling owner and nominal 7 Hz |
| `src/services/motion_arbiter.py` | `MotionArbiter.submit`, `set_mode`, `_evaluate`, `tick`, `get_last_blocked` | Local coverage checks, durable cancellation, serialized output and stop retries; preserves E-STOP, priorities, nominal 20 Hz and safety margins |
| `src/services/motion_execution.py` (new) | `execute_sequence` | Shared bounded MCP execution, preflight, short deadman refresh, structured interruption and cleanup |
| `src/services/distance_motion.py` (new) | `prepare_movement`, `distance_feedback` | Calibration validation and estimated duration; existing timed limits preserved |
| `src/services/mcp_agent_service.py` | Tool schema; `_execute_command`, `_execute_motion_sequence`, `_dispatch_tool`, `_speak_once` | Backward-compatible optional distance parameter, structured results, objective cancellation and one Groq/TTS explanation; tool names and required parameters preserved |
| `servers/aurus_mcp_server.py` | `_init_hardware`, `_execute_motion_sequence`, movement/spin/animation/sensor tools | Shares execution/calibration logic; thread-local feedback; aligns left/right and spin signs with runtime; existing tool names/defaults retained |
| `src/runtime.py` | `RobotRuntime.__init__`, `configure_sensor` | Applies startup center setting to sampler; synchronous safety reassessment and configuration telemetry; startup and other services retained |
| `src/services/behavior_controller.py` | `_explore`, `_showcase`, `finish_exploration` | Cancels exploration at the existing caution reaction threshold; removes obstacle rerouting; stops interrupted showcase steps and avoids false completion announcements |
| `src/services/speech_service.py` | `SpeechService.__init__`, capture loop | Extends existing playback suppression to browser audio; recognizers and audio backends retained |
| `src/web/app.py` | New authorization/configuration/audio SocketIO handlers; disconnect cleanup | Restricts sensor changes to authorized sockets; retains controls/events and telemetry architecture |
| `src/web/templates/index.html` | Awareness panel | Adds five switches, authorization, health, coverage warning and defaults button within existing dashboard |
| `src/web/static/js/evaluation.js` | Sensor rendering/events, telemetry, radar, audio and manual cancellation | Shows unavailable readings truthfully, removes invalid radar points, ends cancelled held commands, tracks browser playback; existing layout/control semantics retained |
| `src/web/static/css/evaluation.css` | Sensor control styles | Small additive styles using existing theme |
| `tests/conftest.py` (new) | `simulation_only` fixture | Forces motor/sensor constructors into simulation even when GPIO is installed |
| `tests/test_final_safety.py` (new) | 47 parametrized test cases | Fake driver/sensors/GPIO/cloud/TTS, authorization, distance and cancellation regression coverage |
| `tests/test_evaluation_core.py` | Exploration fake arbiter and obstacle test | Updates the former rerouting expectation to the explicitly requested stop-and-cancel behavior |
| `AURUS_FINAL_IMPLEMENTATION_REPORT.md` (new) | This report | Implementation, validation, calibration, deployment and recovery documentation |

`run.py`, motor GPIO configuration/driver, camera/face services, legacy AI,
SQLite repository/database, existing TTS backends, requirements and service unit
were not modified. Existing mixed line endings were retained where practical to
keep the diff reviewable.

## E. MCP and Obstacle Safety

1. Voice/text actions use the existing Groq agent and unchanged tool names.
   Dashboard manual commands and behavior commands also use MotionArbiter.
2. The sampler supplies current per-sensor observations. The arbiter checks
   E-STOP, dashboard connection, command expiry, direction coverage and proximity.
3. Existing 20 cm hard-stop and 35 cm caution thresholds remain. The explicit
   front-center degraded mode retains 45/75 cm margins. Forward/reverse caution
   speed limiting remains active. Exploration stops at its existing caution
   reaction threshold instead of choosing another route.
4. An unsafe moving request causes zero output, cancellation, an idle mode, and
   a durable blocked decision. Continued held-button packets and behavior refreshes
   cannot restart movement. A fresh explicit mode/movement request is required.
5. MCP execution checks durable cancellation evidence before refreshing commands,
   aborts the sequence, and stops in cleanup. Runtime MCP retains a 0.2-second
   deadman refresh; standalone execution also uses that conservative refresh.
6. A blocked Groq objective suppresses all remaining tool actions, including
   retries/rerouting and duplicate speech calls. Its normal next completion uses
   `tool_choice="none"` to explain the result. One final explanation is queued.
   Missing/mismatched distance calibration also terminates the objective, so the
   model cannot silently substitute a timed movement.
7. Cloud failure after a block yields a concise local explanation from the real
   result. TTS failure cannot change motor decisions. Action summaries are logged
   through the existing interaction repository when available.

Example fields (the distance is illustrative test data):

```json
{
  "status": "blocked",
  "action": "Movement stopped",
  "reason": "front obstacle 18.2 cm",
  "obstacle_direction": "front",
  "obstacle_distance_cm": 18.2,
  "movement_cancelled": true,
  "motors_stopped": true
}
```

Direction/distance come from structured arbiter decisions, not reason parsing.
Coverage/timeout failures have null obstacle distance; they are not invented
obstacles. `motors_stopped=true` means a driver zero-output/stop operation
returned successfully. There is no independent physical stop feedback; it does
not prove the wheels are stationary. Failed stop output is reported false and
retried by the arbiter while cancellation stays latched. Unexpected safety
evaluation errors also stop and cancel. Interrupted movements never report a
completed estimated distance.

Existing TTS serialization and its bounded queue remain. Existing Pi microphone
playback suppression remains, with an additional browser-audio activity gate and
disconnect cleanup. Browser recognition also ignores playback. Actual acoustic
feedback suppression still requires physical verification.

## F. Ultrasonic Sensor Management

The new **ULTRASONIC SENSOR CONTROL** section provides Front Left (FL), Front
Center (F), Front Right (FR), Rear Left (RL), and Rear Right (RR) switches,
enabled state, valid distance, health, age, error and reduced-coverage warning.

Set `AURUS_SENSOR_OPERATOR_TOKEN` to a long random secret in the Pi's existing
environment and restart during an operator-approved deployment. Enter the token
in the dashboard to authorize the current socket. The input is cleared and is
not saved in browser storage. No configured token means no configuration changes
are accepted. Authorization is lost on disconnect. Use a trusted network or
HTTPS; the existing application is not a general-purpose public authentication
system. No arbitrary GPIO operation is exposed.

Disable immediately removes the cached valid reading, skips future individual
polling, publishes Disabled, updates radar/telemetry and synchronously reassesses
active motion. A read already in flight may finish but cannot publish across a
disable/re-enable generation change. Enable publishes Initializing and requires
a new valid measurement. Restore defaults invalidates all readings before
reinitializing, so it cannot automatically restart movement.

| Movement | Required healthy, fresh sensors |
| --- | --- |
| Forward | FL, F, FR; only explicitly disabled F permits the existing FL/FR degraded mode |
| Backward | RL, RR |
| Left | FL, RL |
| Right | FR, RR |
| Diagonal | Union of the applicable forward/rear and lateral requirements |
| Rotation | All five sensors, plus a 20 cm obstacle check around the rover |

Disabled, initializing, unhealthy and stale sensors never imply clearance. A
faulty enabled center sensor does not automatically select degraded mode.
Rotation is conservatively unavailable with disabled center coverage.

Statuses are Healthy, Unhealthy, Disabled, Initializing and Stale. Missing/stuck
echo, invalid/nonfinite/out-of-range samples and repeated large inconsistencies
produce errors and failure counts. Three consecutive changes exceeding 100 cm
trigger the inconsistency diagnostic; this is a conservative software heuristic,
not a physically validated sensor model. A subsequent valid sample can recover
health without changing enabled state. Data older than 0.3 seconds is stale.
Measurement age starts before the read, not after an entire five-sensor cycle.
Diagnostics are published without per-cycle warning logging.

Settings are runtime-only. Restart restores all enabled except F when existing
`AURUS_FRONT_SENSOR_ENABLED=false` is configured. The defaults button restores
that startup configuration. Calibration settings are persistent only when the
operator places them in the existing environment/.env; no file is written by
the switches.

## G. Time-Based Distance Movement

```text
duration_seconds = requested_distance_cm / calibrated_speed_cm_per_second
```

Both move_rover implementations retain `direction`, `speed=1.0` and
`duration=1.0`, with an optional `distance_cm`. Direction remains the only required
parameter. Distance overrides duration and supports forward/backward/left/right.
Timed diagonal movement remains supported.

Required environment settings for a requested direction:

```dotenv
AURUS_DISTANCE_MOTOR_SPEED=
AURUS_DISTANCE_OUTPUT_SCALE=
AURUS_DISTANCE_VECTOR_MULTIPLIER=
AURUS_DISTANCE_FORWARD_CM_S=
AURUS_DISTANCE_BACKWARD_CM_S=
AURUS_DISTANCE_LEFT_CM_S=
AURUS_DISTANCE_RIGHT_CM_S=
```

Blank means uncalibrated. Only the rate for the requested direction is required,
together with the three motor settings. Record the exact normalized requested
speed, current `MOTOR_OUTPUT_SCALE` and `MOTOR_SPEED_MULTIPLIER`. The requested
speed must match the calibration; no linear PWM scaling is assumed. Timed
commands keep their 0–1 speed clamp and 0.1–10 second duration bounds. A distance
whose computed duration is outside those bounds is rejected rather than silently
shortened. NaN/infinite inputs and nonpositive distances are rejected.

Example commands after calibration:

- “AURUS, move forward 50 centimeters.”
- “AURUS, move backward 30 centimeters.”
- “AURUS, move left 20 centimeters.”
- “AURUS, move right 100 centimeters.”

If calibration uses a speed other than 1.0, explicitly request that speed or pass
it to the tool; the existing default remains 1.0. The result reports
`requested_distance_cm`, `duration_s`, `distance_is_estimated=true`, and
`actual_distance_cm=null`. A successful uninterrupted movement without safety
speed limiting reports the requested value as `estimated_distance_cm`. If speed
was limited or movement interrupted, that estimate is null, with an explanation.
Legacy position fields remain simulated model coordinates, not measured odometry.

### Operator calibration procedure (not performed by Codex)

1. Verify sensors, E-STOP, wheel direction and the existing timed controls in a
   controlled, clear area, with an operator ready to stop the robot.
2. Select one fixed motor speed and record output scale/vector multiplier. Keep
   the same wheels, load, supply and floor conditions.
3. Mark the starting position. Request a short timed movement through the existing
   dashboard/arbiter. Measure displacement with a tape, and record duration.
4. Repeat and check consistency for each direction. Do not use obstacle-limited
   or interrupted runs as calibration samples. Compute measured cm / seconds.
5. Enter measured rates and the associated settings in the Pi's existing .env.
   Restart only as part of a deliberate deployment, then test short distance
   requests before longer ones.
6. Recalibrate after motor/output configuration, battery/load, wheel or floor
   changes. Lateral motion must be verified on the actual wheel assembly; an
   existing legacy command guide contradicts the stated mecanum hardware.

Illustration only: 40 cm measured in 2 seconds gives 20 cm/s, so a 50 cm request
would use 2.5 seconds. These numbers are not measured AURUS calibration values.
No wheel encoders, localization, actual travel measurement or precision
positioning are claimed. Safety checks remain active throughout the duration.

## H. Automated Test Results

Tests ran on Windows/Python 3.12 with fake drivers, GPIO, sensors, Groq and TTS.
The pytest fixture forces hardware constructors into simulation, including when
tests are later run on a Pi. The application startup, service and physical GPIO
voltage test were not executed. No dependencies were installed.

| Exact command | Observed result |
| --- | --- |
| `python -m pytest -q` (first sandbox run) | 81 passed, 6 failed, 2 skipped; temporary SQLite/audio files inaccessible in Windows sandbox |
| `python -m pytest -q` (filesystem-approved rerun after initial safety changes) | 87 passed, 2 skipped |
| `python -m pytest -q tests/test_evaluation_core.py -k 'MotionArbiter or MCPAgentSafety'` | 16 passed, 37 deselected |
| `python -m pytest -q tests/test_evaluation_core.py -k 'MotionArbiter or MCPAgentSafety or SensorSampler or WebAdapter'` | 19 passed, 34 deselected; repeated after integration, same result |
| `python -m pytest -q tests/test_final_safety.py tests/test_evaluation_core.py -k 'final or MotionArbiter or MCPAgentSafety or SensorSampler or WebAdapter or Curiosity'` | 62 passed, 29 deselected |
| `python -m pytest -q` (first expanded full run) | 125 passed, 2 skipped |
| `python -m pytest -q tests/test_final_safety.py` | 46 passed before the final exception-path test was added |
| `python -m pytest -q -rs` (expanded failure-path run) | 133 passed, 2 skipped |
| `python -m pytest -q -rs` (final source state) | **134 passed, 0 failed, 2 skipped**, 7.80 seconds |
| `node --check src/web/static/js/evaluation.js` | Passed, including final source state |
| `python -m compileall -q src servers tests` | Passed, including final source state |
| `git diff --check` | Used throughout; initial whitespace/line-ending findings corrected before final delivery |

The two skips are existing tests requiring unavailable OpenCV: Picamera BGR888
conversion and Haar/SFace embedding. The environment also emits an existing
pytest-asyncio default fixture-loop-scope deprecation warning. The original six
filesystem failures disappeared with approved access; there are no outstanding
test failures in the final full run.

Coverage includes directional obstacle data, cancellation and no automatic
resume; threaded active MCP interruption; emergency stop; driver-stop failure
and retries; unexpected sensor errors; independent polling; disabled/in-flight
invalidation; re-enable freshness; stale/invalid/timeout/inconsistent health;
safe JSON telemetry; authorization rejection and restore defaults; Groq tool
results, suppressed retries and one TTS explanation; cloud/TTS failure;
conversion/calibration/limits; estimated-motion interruption and speed limiting;
and the standalone MCP movement/sensor path.

Not executed: actual Groq API requests, real audio synthesis/playback/capture,
browser visual/device testing, Pi GPIO/echo voltage checks, physical sensor
coverage, measured stop distance, motor motion/calibration, camera/face hardware,
service restart, deployment or exhibition rehearsal. These remain operator work.

## I. Raspberry Pi Deployment

These are instructions for the operator, not commands executed in this task.
The laptop and Pi have previously used different repository URLs. **Verify
repository identity and history before any pull or merge.** Do not assume a
remote called origin or a matching directory name proves identity.

On the laptop, review the changes and report, run tests, and manually create a
review branch/commit and publish it to the verified GitHub repository when ready.
Select intended files explicitly. No code from this task is on GitHub yet.

On the Pi, inspect the existing service before choosing a checkout path:

```bash
sudo systemctl cat aurus.service
sudo systemctl status aurus.service --no-pager
cd /path/verified/from/the/service
git remote -v
git status --short
git branch --show-current
git rev-parse HEAD
git log --oneline -5
```

The checked-in unit currently references `/home/tt-11/AURUS-3.0`, but the installed
unit must be checked. Compare remote owner/repository, expected reviewed branch,
and known commit IDs with the laptop. Do not publish credential-bearing URLs.
If URLs or histories differ, stop and resolve identity deliberately. Do not
force a pull, change remotes blindly or merge unrelated histories. A separately
verified sibling clone and deliberate service migration may be appropriate.

Before changing a verified checkout, record its current commit and preserve any
uncommitted work. Create a recovery branch pointer with a unique name:

```bash
git branch recovery/pre-aurus-update-YYYYMMDD-HHMM HEAD
git diff --binary > ../aurus-pre-update-working.patch
git diff --cached --binary > ../aurus-pre-update-index.patch
git ls-files --others --exclude-standard
```

Copy any listed untracked files and back up the existing .env, database, service
configuration and dependency versions separately. Patches do not include
untracked/ignored files. If the tree is dirty, finish preserving/reviewing it
before proceeding; do not discard it to make an update succeed.

With identity verified, fetch the explicitly chosen remote and inspect the
reviewed remote branch (replace uppercase names with verified values):

```bash
git fetch VERIFIED_REMOTE
git log --oneline HEAD..VERIFIED_REMOTE/REVIEWED_BRANCH
git diff --stat HEAD VERIFIED_REMOTE/REVIEWED_BRANCH
git diff HEAD VERIFIED_REMOTE/REVIEWED_BRANCH -- src servers tests .env.example
git merge-base --is-ancestor HEAD VERIFIED_REMOTE/REVIEWED_BRANCH
```

Only if ancestry is as expected, the worktree is clean and review is satisfactory,
stop the service and apply the fast-forward update:

```bash
sudo systemctl stop aurus.service
git merge --ff-only VERIFIED_REMOTE/REVIEWED_BRANCH
.venv/bin/python -m pip check
.venv/bin/python -m pytest -q -rs
```

Do not override a failed fast-forward. Inspect divergence and choose a reviewed
integration/recovery path. Tests use simulation fixtures; do not run
`tests/test_ultrasonic_voltage.py` as part of this procedure. If pytest is absent,
provision the test environment deliberately or run tests in a separate environment.
No new runtime dependency was added, so do not blindly upgrade packages. Inspect
requirements and the existing Pi system-site-packages environment. If a dependency
change is actually needed, review it and preserve the previous package versions.

Add the operator token to the existing environment, and add calibration values
only after measurement. Preserve existing API keys, GPIO settings, audio settings
and database. Then:

```bash
sudo systemctl start aurus.service
sudo systemctl status aurus.service --no-pager
sudo journalctl -u aurus.service -n 100 --no-pager
sudo journalctl -u aurus.service -f
```

Open the existing dashboard address/port. Check connection, camera, microphone,
TTS, all five sensor statuses, token rejection/authorization, switches, reduced
coverage and E-STOP before authorizing any physical movement. Verify blocked
telemetry and no automatic resume. Complete the exhibition checklist under
supervision. Never run the standalone MCP hardware owner beside aurus.service.

## J. GitHub Recovery and Rollback

Use the Pi's recorded pre-update commit as its recovery baseline. The laptop
baseline above is useful only if repository identity/history actually matches.
Find known working code using the recovery branch, GitHub history and:

```bash
git log --oneline --decorate -20
git reflog --date=iso
git show --stat KNOWN_GOOD_COMMIT
git diff KNOWN_GOOD_COMMIT HEAD -- src servers .env.example requirements.txt
```

Preserve current tracked, staged, untracked and ignored state as described above.
Create a branch pointer to the current code before recovery:

```bash
git branch recovery/before-recovery-YYYYMMDD-HHMM HEAD
```

For a reviewed published update, prefer a new reverting commit on a recovery
branch, keeping shared history intact:

```bash
git switch -c recovery/revert-aurus-update
git revert UPDATE_COMMIT
```

For multiple update commits, review the exact list and revert newest to oldest.
Merge commits require deliberate mainline selection; do not guess `-m`.
If conflicts occur, inspect and resolve them, then use `git revert --continue`;
`git revert --abort` exits that attempted revert without discarding unrelated
pre-existing work. Run the simulation suite and review the resulting diff before
manually committing/publishing any recovery edits and redeploying through the
verified GitHub repository. `git revert` normally creates its own reverting
commit when conflict-free; inspect it before pushing.

For an immediate known-version checkout, after preserving work and stopping the
service, create a new uniquely named branch at the known-good commit:

```bash
sudo systemctl stop aurus.service
git switch -c recovery/known-good-YYYYMMDD-HHMM KNOWN_GOOD_COMMIT
.venv/bin/python -m pip check
.venv/bin/python -m pytest -q -rs
sudo systemctl start aurus.service
sudo systemctl status aurus.service --no-pager
sudo journalctl -u aurus.service -n 100 --no-pager
```

The older commit may lack the new simulation fixture. Before running its tests
on a Pi, verify all hardware constructors are mocked/forced to simulation; if
not, run the old tests on the laptop instead. Git should refuse branch switching
that would overwrite local work; preserve that work rather than forcing it.

Check dashboard health, E-STOP, sensor readings, camera/audio and a controlled
operator-approved rehearsal to verify service recovery. Publish a reviewed revert
to restore the shared branch permanently when appropriate; the known-good branch
alone does not alter the GitHub deployment branch.

Git restores tracked code. It may not restore pip/apt dependencies, the SQLite
database, environment variables/.env, downloaded models, systemd overrides,
GPIO/wiring or other hardware configuration. Recover those from separate backups
as needed. No force-push, hard reset, destructive clean or automatic rollback is
recommended or was performed.

## K. Known Limitations

- No encoders: distance is a time estimate, affected by acceleration, slippage,
  battery/load/floor and direction. Actual travelled distance stays unknown.
- No measured calibration is supplied. Distance movement is intentionally rejected
  until configured at the matching speed/output settings.
- Five angled ultrasonic transducers do not provide complete body coverage.
  Degraded center mode is inherited, not newly physically validated. Conservative
  rotation restrictions may limit exploration when sensors are off.
- Echo timeouts now fail closed. Open space/no echo can therefore restrict motion;
  nominal 7 Hz is a target, not a measured hardware rate. Long reads, filtering or
  Linux scheduling may make samples stale. Verify actual Pi timing without
  weakening safety margins to hide a fault.
- The inconsistency heuristic needs validation with the real mount/floor/environment.
- Natural spoken explanations depend on Groq availability. A local result-based
  fallback exists after a block; safety does not depend on either explanation.
- TTS needs an available backend and a working speaker/audio route. Browser audio
  needs browser permission. Acoustic feedback suppression needs real verification.
- The new token protects sensor configuration events only; it does not retrofit
  authentication onto all existing dashboard controls. Use the existing dashboard
  on a trusted network, with HTTPS when transporting secrets.
- The standalone MCP client controls its own conversation/TTS behavior. Do not
  run its GPIO process concurrently with the main runtime.
- Camera, microphone, face recognition and real Groq regressions were not tested
  on hardware; two OpenCV-dependent automated checks were skipped here.
- No Raspberry Pi hardware tests, service operations, deployment or real motor
  movement were performed. Full exhibition readiness is not established by mocks.

## L. Exhibition Checklist

- [ ] Raspberry Pi boots with correct power and expected environment.
- [ ] `aurus.service` is active with no repeated restart/errors in logs.
- [ ] Dashboard connects and all existing controls/layout remain usable.
- [ ] FL, F, FR, RL and RR show fresh, plausible readings or truthful faults.
- [ ] Unauthorized sensor changes are rejected; authorized switches work individually.
- [ ] Disable invalidates readings/radar and restricts affected movements immediately.
- [ ] Re-enable waits for fresh data; defaults restore the documented startup settings.
- [ ] Health/timeout/stale/reduced-coverage warnings are understood by the operator.
- [ ] Camera stream and face detection/recognition remain functional.
- [ ] Microphone, wake phrase and speech recognition work without self-triggering.
- [ ] Supervised forward and backward timed movement work with confirmed directions.
- [ ] Supervised left/right movement is physically possible and correctly directed.
- [ ] Obstacle stopping cancels movement; clearing the obstacle does not restart it.
- [ ] MCP returns blocked direction/distance and truthful stop acknowledgement.
- [ ] Groq gives one concise explanation without retrying or rerouting.
- [ ] Existing TTS speaks once through the intended speaker/browser route.
- [ ] Distance calibration is measured/documented at the exact configured settings.
- [ ] Short estimated-distance commands work and report estimates, not odometry.
- [ ] Missing calibration/coverage and interrupted movement fail safely.
- [ ] E-STOP latches in every mode, overrides commands and requires explicit clearing.
- [ ] Known-good commit, backups and reviewed recovery commands are available.

## M. FIT Expo Demo and Prepared Voice — 2026-11-05

This follow-up implements the planned one-button demo with the revised FIT Expo
wording. Its separate recovery baseline was a clean `main` checkout at
`d5342da4dc17d4c09e90e2436ea55d67b97240d8`. That commit already contained the earlier
implementation described above. This follow-up remains uncommitted.

The introduction now says **Faculty of Information Technology Expo**, and the
closing says **FIT Expo**. The sentence “Please ask them for a demonstration of
my features” was removed. The selected Codex PLAN.md was also updated at the
user's request; that file is outside this repository.

### Prepared speech and replay

Seven WAV clips were actually rendered using the already-installed **Microsoft
Zira Desktop** English voice at rate -1. Each clip is mono, 16-bit PCM, normalized
to -1 dBFS peak with no clipped samples. The full narration preview is **61.943
seconds**, including short pauses. No voice package/model was installed and no
network synthesis request was made. Rendering wrote audio files only; no speaker
playback or robot hardware was operated.

The complete cache is committed only when the operator chooses to stage/publish
it: `assets/fit_expo/introduction.wav`, `movement.wav`, `turn_left.wav`,
`turn_right.wav`, `capabilities.wav`, `closing.wav`, `blocked.wav`,
`fit_expo_preview.wav`, `manifest.json`, and `README.md` (all in that directory).
The cache must be deployed alongside the new code. The Pi needs its existing
WAV player/audio route, not the Windows voice. Integrity checks reject missing,
corrupt or stale recordings before the demo can start.

**Start FIT Expo Demo** replays those clips without runtime TTS synthesis or
Groq requests. The existing Pi/PC/Auto selector remains. During a run its route
is fixed; stop, switch output, and replay to change speakers. Cached levels are
not amplified again by the normal TTS gain. Actual room loudness still depends
on the speaker/amplifier and needs an operator sound check.

### Demonstration behavior and controls

- Existing showcase entry points now use the FIT Expo demo; the old strafe and
  diagonal choreography was removed. Other ordinary manual controls are retained.
- Straight movement pulses are 0.20 seconds each at magnitude 0.20; rotation
  pulses are 0.15 seconds each at magnitude 0.20. These are time commands, not
  measured travel distances/angles or guarantees of slow physical motion.
- All demo requests have zero lateral velocity and go through MotionArbiter,
  with priority 40 and a 0.15-second deadman refresh. Existing thresholds and
  GPIO assignments are unchanged. Additional demo clearance is at least 60 cm,
  or 75 cm for degraded front-center coverage, respecting larger existing limits.
- Any blocked gesture, insufficient coverage or arbiter cancellation cancels all
  later gestures. One cached explanation plays; the remaining introduction
  finishes stationary. Disabled F therefore prevents rotation without being
  silently re-enabled. There is no retry/rerouting or automatic movement resume.
- Pi narration waits for local playback. PC narration is delivered only to the
  initiating dashboard and waits for an utterance-specific success/error ack.
  Wrong-client, duplicate and stale acknowledgements are rejected. Each speech
  step has a 45-second timeout; unrecoverable audio failure ends the demo.
- Stop Demo, E-STOP, initiating-dashboard disconnection and shutdown cancel the
  sequence. Browser demo audio is stopped; an active short Pi utterance may
  finish. E-STOP remains latched. Completion leaves the robot idle/stopped.
- Concurrent agent/manual/listening requests and automatic greetings are gated
  during the demo. Late conversation responses do not enter its speech queue.
  Camera/face display and telemetry continue. Live demo stage/reason is visible.

Additional or modified files in this follow-up:

| File | Purpose |
| --- | --- |
| `src/services/demo_script.py` | Reviewed script and cache/hash/PCM validation |
| `src/services/exhibition_demo.py` | Cancellable narration/gesture coordinator |
| `scripts/prepare_demo_audio.py` | Offline file rendering and peak normalization on Windows |
| `src/services/tts_service.py` | Backward-compatible cached jobs, route handling and browser acknowledgements |
| `src/runtime.py` | Start/stop, activity guards, telemetry and immediate E-STOP ordering |
| `src/services/behavior_controller.py` | Delegate showcase to demo; remove old choreography; suppress demo-time greetings |
| `src/services/mcp_agent_service.py` | Busy state and rejection of new agent execution during demo |
| `src/web/app.py` | Start/stop/ack events, targeted audio and disconnect cleanup |
| `src/web/static/js/evaluation.js` | Button/status, browser ack, cancellation and recognition suppression |
| `src/web/templates/index.html` | FIT Expo button/status text |
| `pytest.ini` | Discover demo tests |
| `tests/test_exhibition_demo.py` | 21 fake-hardware/audio and cache validation cases |
| `tests/test_final_safety.py` | Trigger the threaded obstacle test after actual fake drive output, avoiding wall-clock timer races |
| `assets/fit_expo/` | The ten audio/manifest/documentation assets listed above |
| `AURUS_FINAL_IMPLEMENTATION_REPORT.md` | This follow-up record |

### Actual follow-up validation

- `python scripts/prepare_demo_audio.py`: first sandbox attempt failed because
  its temporary files were inaccessible; approved-access rerun rendered seven
  clips and a 61.943-second preview successfully, without playback.
- `python -m pytest -q tests/test_exhibition_demo.py`: **21 passed**. Repeated
  after the final-control-cycle cancellation fix: **21 passed**.
- Initial full integration check: **134 passed, 2 skipped**, before demo tests
  were added to discovery.
- Expanded full runs identified a final-pulse cancellation edge case and then a
  scheduling-sensitive existing timer-based test. The first was fixed in demo
  cancellation handling; the second now injects its obstacle only after fake
  movement starts and keeps sampling active. Safety assertions were retained.
- Final `python -m pytest -q -rs`: **155 passed, 0 failed, 2 skipped**, 14.23 seconds.
  Skips remain the existing unavailable-OpenCV checks.
- `node --check src/web/static/js/evaluation.js` and
  `python -m compileall -q src scripts/prepare_demo_audio.py tests`: passed.
  Prepared text, WAV integrity, durations, hashes, levels and no sample clipping
  were also checked programmatically. Final diff whitespace checks were performed.

Before FIT Expo, an operator must listen to the preview, check the actual speaker
level and PC fallback, then rehearse the brief straight/turn gestures on the
actual floor. Low normalized speed does not bypass the driver's high PWM floor;
verify tire security before allowing turns. Physical movement, stop latency,
acoustic feedback and real browser/device playback remain unverified by mocks.
No Pi deployment, service restart, real motor movement, Git commit or push was
performed in this follow-up.

## N. Ultrasonic Echo Timing Correction — 2026-11-05

The operator reported FL and F showing `echo stuck high (fall timeout)` even
while stationary, with FR/rear readings healthy. This is missing valid front
coverage, not evidence that a large valid distance is an obstacle. An actual
electrical/module fault cannot be confirmed or excluded from that screenshot.
The existing motor restrictions remain fail-closed.

Inspection found that the physical driver still attempted three measurements
per sensor separated by only 5 ms, triggered without first checking Echo idle,
and allowed just 20 ms for the falling edge. The
[HC-SR04 datasheet](https://cdn.sparkfun.com/datasheets/Sensors/Proximity/HCSR04.pdf)
recommends measurement cycles longer than 60 ms and specifies a 4 m range.
At the existing speed-of-sound conversion, a 4 m return takes approximately
23.3 ms, exceeding that old fall timeout.

Changes in `src/hardware/sensors.py`:

- Serialize physical reads and enforce at least 65 ms between triggers of the
  same transducer. Stagger different transducers by at least 40 ms as a
  conservative software default; actual cross-talk still requires Pi verification.
- Require Echo to become LOW before triggering, with a bounded 5 ms idle wait.
  A signal already HIGH is reported as `echo high before trigger (sensor busy or stuck)`;
  the driver does not retrigger or interpret an old HIGH as a new measurement.
- Capture a new rising/falling cycle, retain the bounded 20 ms rise wait, and
  allow up to 30 ms for a falling edge. Completed out-of-range/nonfinite/zero
  pulses remain invalid. Timeouts still become errors, never 400 cm clearance.
- Use one fresh physical ping per sensor per sampler pass instead of the rapid
  three-ping burst. Explicit multi-sample/median calls remain available and
  respect the 65 ms spacing. Existing repeated-inconsistency diagnostics remain.

Five healthy sensors are consequently refreshed at approximately 5 Hz rather
than promising the sampler's nominal 7 Hz target. The synthetic 390 cm
five-sensor round remains below the unchanged 0.3-second freshness limit.
Real Linux timing, mount reflections and sensor response require verification.
No GPIO assignment, motor threshold, E-STOP, freshness bound, invalid-reading
restriction or automatic-resume behavior was weakened. No sensor was silently
disabled. The previous FIT Expo demo changes and other uncommitted work were
preserved.

`tests/test_ultrasonic_timing.py` adds 15 synthetic-clock/GPIO cases covering
fresh echoes through 390 cm, busy/high input before triggering, missing/stuck
signals, completed out-of-range pulses, per-channel spacing, five-sensor timing,
recovery, directional restrictions and no automatic resume. The existing
private-driver test in `tests/test_final_safety.py` now initializes its fake
timing state; `pytest.ini` includes the new test file. No physical GPIO test ran.

Actual commands/results:

- `python -m pytest -q tests/test_ultrasonic_timing.py tests/test_final_safety.py -k 'timing or gpio_missing or health_fault or stale'`:
  **23 passed, 39 deselected**.
- `python -m pytest -q -rs`: **170 passed, 0 failed, 2 skipped**, 12.18 seconds.
  The skips remain the two unavailable-OpenCV checks.
- `python -m compileall -q src/hardware/sensors.py tests/test_ultrasonic_timing.py`:
  passed. Diff whitespace checks were performed before delivery.

After a deliberate code update on the Pi, keep the robot stopped and inspect
FL/F status first. Fresh valid measurements should restore Healthy status;
movement still requires a fresh user command after cancellation. Persistent
`high before trigger` while stationary requires an operator to inspect the
module, common ground, connectors and echo voltage-divider wiring with power
off. A genuinely held-HIGH line is not safely repairable by fabricating distance
or bypassing coverage checks. Do not run the existing trigger-only voltage test
beside the running application; it is not part of this patch's validation.

These changes are local and uncommitted. No Pi service was restarted, no update
was deployed, and no real sensor or motor was operated.

## O. Louder Cloud Neural Demo Audio — 2026-11-05

The operator requested stronger, clearer narration for low-quality speakers and
explicitly excluded local TTS generation. This follow-up started on a clean
`main` at `c8157a831b8d9452b61607e055d23d3024d5e11c` and replaces the prepared Zira
recordings, while preserving the reviewed FIT Expo script and existing replay
interface. Ordinary conversation TTS backends are unchanged.

All seven clips were actually generated by **Microsoft Edge online neural TTS**,
voice **en-US-GuyNeural**, rate **-8%**. No local speech synthesizer or local voice
model was used. An isolated temporary build-tools directory received edge-tts
7.3.1 and imageio-ffmpeg 0.8.1, which supplies FFmpeg 9.0.2. No application/Pi
dependency manifest or installed application environment was changed. Pip printed
a pre-existing FastAPI/Starlette resolver conflict warning; the tools were
installed into the separate target directory, not the runtime environment.

Preparation now applies speech presence EQ, bass/rumble reduction, compression,
two-pass EBU R128 normalization and oversampled peak limiting, then saves mono
48 kHz 16-bit PCM WAV. This raises average speech level rather than overloading
peaks. The generator targets -12 LUFS with 1.5 LU tolerance and conservative
true-peak headroom. Successful raw cloud recordings are temporarily cached by
text/voice/rate hash so mastering retries do not require repeated synthesis.
There is deliberately no local-generation fallback.

Measured final results:

| Clip | Previous local audio LUFS | New neural audio LUFS | New true peak dBTP |
| --- | --- | --- | --- |
| Introduction | -18.08 | -12.41 | -2.00 |
| Movement | -17.84 | -12.05 | -2.00 |
| Turn left | -16.10 | -12.51 | -1.99 |
| Turn right | -19.13 | -13.36 | -2.00 |
| Capabilities | -17.14 | -12.12 | -2.00 |
| Closing | -16.87 | -12.64 | -1.99 |
| Blocked notice | -16.83 | -11.96 | -2.00 |

The new full preview is **59.886 seconds**, measured at **-12.39 LUFS / -1.99
dBTP**. Clip loudness increases are approximately **3.6–5.8 LU**. Programmatic
PCM checks found no clipped samples. These measurements do not establish
physical room audibility: speaker volume, amplifier power, correct output
routing and distortion still need an operator check on the actual equipment.

`scripts/prepare_demo_audio.py` is now a cloud-only preparation script with
optional FFmpeg path selection. The `assets/fit_expo/README.md`, manifest, seven
WAV clips and preview were updated. Runtime replay continues to read those
files through the existing Pi/PC/Auto routes, without API calls or additional
runtime gain. The complete assets folder and manifest must be deployed together.
New assets are staged and validated before publication; the manifest is copied
last so an incomplete replacement cannot validate as current audio.

Actual preparation command (with isolated build tools on PYTHONPATH):

```powershell
python scripts/prepare_demo_audio.py
```

Early preparation runs rejected FFmpeg JSON parsing or loudness/true-peak
measurements; no working cache was replaced by those failed attempts. Parsing
and oversampled limiting were corrected, measured thresholds were verified, and
the final run successfully published all seven validated neural clips and the
preview. Audio was rendered/analyzed only; no speaker playback occurred here.

Sources: [Edge TTS project](https://github.com/rany2/edge-tts),
[FFmpeg audio filters](https://ffmpeg.org/ffmpeg-filters.html).

## P. Operator-Selected Remaining-Sensor Policy — 2026-11-05

The operator explicitly requested that missing or switched-off sensors stop
restricting movements and that remaining sensors be used normally. This replaces
the original full-coverage requirement described in E/F/N. It applies to manual
controls, voice/MCP, exploration and FIT Expo movements.

```dotenv
AURUS_REQUIRE_FULL_SENSOR_COVERAGE=false
```

False is the application default. True restores strict coverage checks and the
previous degraded-front margins. MotionArbiter's class constructor retains a
strict default for backward-compatible callers; both actual composition roots
explicitly pass the configured application policy.

Under the default remaining-sensor policy:

- Disabled, initializing, faulty, missing and stale readings are excluded from
  movement calculations; they are not replaced by infinity, zero or fabricated
  safe distances. Polling/error/health diagnostics remain truthful.
- Partial coverage does not itself cancel or reject a movement. Available valid
  measurements in the relevant direction still enforce the ordinary 20 cm hard
  stop and 35 cm forward/reverse caution rules. Rotation checks remaining valid
  measurements. Disabling F no longer imposes 45/75 cm degraded limits in this
  mode. Strict opt-in mode preserves those limits.
- If no valid reading exists in a requested direction but other sensors remain
  usable, the movement is allowed with a reduced-coverage warning. That direction
  is **unobserved**, not confirmed clear. MCP reports null for an unavailable
  directional distance and does not fabricate a `front_clear=true` result.
- If every sensor is unavailable, movement is still stopped: there are no
  measurements left to use. E-STOP, command expiry/deadman, disconnection stops,
  priority arbitration and valid-obstacle cancellation remain active.
- A real obstacle block still cancels the request, and recovery requires a fresh
  user command. Sensor toggles keep authorization and invalidate cached readings.
- FIT Expo still uses its short movement pulses and additional 60 cm clearance
  margin wherever a valid relevant reading exists. Missing sensors alone no
  longer prevent gestures. Actual nearby readings can still select speech only.
- The dashboard displays **unavailable sensors ignored** rather than claiming
  directions are restricted. Complete loss displays **movement stopped**. Raw
  coverage/health telemetry remains available, with a `coverage_required` policy
  flag so consumers can distinguish warnings from strict restrictions.

This policy leaves blind areas and can permit an unobserved direction. An expo
hall without large obstacles can still contain people and smaller objects;
operator supervision is required. The software does not establish that the path
is safe merely because a sensor is absent. No automatic physical test was run.

Source changes in this follow-up:

| File | Changes |
| --- | --- |
| `.env.example`, `src/config.py` | Document/load the default-false coverage policy |
| `src/core/models.py` | Select available valid distances and optional directional clearances |
| `src/services/motion_arbiter.py` | Conditional strict coverage, remaining-reading obstacle/caution checks, complete-loss stop |
| `src/runtime.py` | Pass policy, publish policy telemetry, truthful available-distance status text |
| `src/services/behavior_controller.py` | Exploration uses remaining front readings |
| `src/services/exhibition_demo.py` | Demo coverage is conditional; valid clearance margins retained |
| `src/services/mcp_agent_service.py`, `servers/aurus_mcp_server.py` | Consistent policy/tool feedback and truthful optional distances |
| `src/web/static/js/evaluation.js` | Warning versus restriction text and complete-loss message |
| `scripts/prepare_demo_audio.py` | Cloud neural synthesis, mastering, measurement validation and cache publication |
| `assets/fit_expo/` | README, manifest, seven neural clips, complete preview |
| `tests/test_exhibition_demo.py` | Neural audio format/loudness/PCM checks and generator measurement/error tests |
| `tests/test_remaining_sensor_policy.py` | 26 policy cases across manual, MCP, demo, exploration and strict opt-in |
| `tests/test_final_safety.py`, `pytest.ini` | Telemetry harness compatibility and new test discovery |
| `AURUS_FINAL_IMPLEMENTATION_REPORT.md` | This current behavior and verification record |

Actual verification:

- `python -m pytest -q tests/test_exhibition_demo.py`: **24 passed**.
- `python -m pytest -q tests/test_remaining_sensor_policy.py tests/test_exhibition_demo.py`:
  **48 passed** before two additional exploration/toggle cases were added.
- First expanded full run: 198 passed, 1 failed, 2 skipped; the new exploration
  test omitted its required fake vision-analyzer argument. The test fixture was
  corrected without weakening assertions or changing production behavior.
- Final `python -m pytest -q -rs`: **199 passed, 0 failed, 2 skipped**, 13.54 seconds.
  The skips remain unavailable-OpenCV tests.
- `node --check src/web/static/js/evaluation.js` and
  `python -m compileall -q src servers scripts/prepare_demo_audio.py tests/test_remaining_sensor_policy.py`:
  passed. Final prepared-asset validation and diff whitespace checks were performed.

No Pi startup/service setting, actual speaker volume, GPIO assignment, motor
driver configuration or API key was changed. No Pi deployment, physical motor
operation, commit or push was performed. Restart a manually launched application
after deliberately transferring the reviewed code/assets so the new environment
policy is loaded. The earlier manual-start preference is preserved.

## Q. Groq Orpheus Speech Integration — 2026-11-05

**Historical: normal Groq selection and cached demo behavior below were
superseded by the operator-requested rollback/live-demo change in Section R.**

This follow-up started on `main` at clean commit
`46359db6e1109d07433118edf3c91ed8d6e65235`. It adds explicit Groq speech selection
without changing sensor/motor policy, hardware pins or the installed Pi service.

```dotenv
AURUS_TTS_BACKEND=groq
GROQ_TTS_MODEL=canopylabs/orpheus-v1-english
GROQ_TTS_VOICE=troy
AURUS_TTS_VOLUME_PERCENT=0
AURUS_TTS_GAIN_DB=6
```

Groq's documented English Orpheus API supports WAV output, six English voices
and at most 200 input characters per request. The new adapter splits longer
text at sentence/word boundaries, validates 16-bit PCM responses, and joins
compatible chunks with a short pause before playback. Raw SDK errors, API keys
and user input are excluded from adapter error messages. Account/model access,
rate/quota and audio-format errors produce actionable messages.

Explicit `groq` overrides an installed Piper model. It stays selected after a
failed request, reports degraded health and permits a subsequent retry; it
does not unexpectedly switch to local speech or replay partial text. Missing
key/SDK or invalid voice/model prevents ordinary speech from being queued.
Unset/`auto` retains the previous provider-selection behavior. The existing
queue, playback suppression event and Pi/browser/auto WAV routing are reused.
The runtime health includes the selected speech model. Gain is limited against
the PCM peak with 1 dB sample headroom rather than blindly clipping at large
boost settings. This does not prove acoustic loudness on the real speaker.

The FIT Expo demo still uses the already-supplied **Microsoft Guy neural WAV
cache** offline. No WAV files or manifest were replaced in this change, and
cached replay makes no Groq calls or additional runtime amplification. The
preparation script now optionally supports `--provider groq --voice troy` with
the same loudness mastering, validation and staged publication. Its cloud
source-cache identity includes provider/model. A failed generation leaves the
working cache in place. New Groq demo clips have **not** been synthesized here;
their duration, loudness and room audibility require a deliberate preparation
run and operator review.

`scripts/check_groq_tts.py` is a separate speech-only utility. It loads the
private `.env`, creates `logs/groq-tts-test.wav`, and reports the selected
model/voice/duration without playing audio or initializing the robot runtime.
The setup guide covers replacing the exposed key privately, verifying model
access, reviewing/publishing the laptop changes, matching Pi Git remotes and
history, disabling boot startup, confirming real ALSA devices, generating one
speech test, and manually launching/stopping AURUS. A missing USB microphone
cannot be repaired by changing its card number in `.env`.

Complete modified/new file set for this follow-up:

| File | Change |
| --- | --- |
| `src/services/groq_tts_service.py` (new) | English model/voice validation, bounded cloud synthesis, safe errors and PCM joining |
| `src/services/tts_service.py` | Explicit provider selection, Groq worker branch, health, existing WAV routes and peak-limited gain |
| `src/runtime.py` | Speech model in health output |
| `.env.example` | Groq speech settings, playback override and current vision model example |
| `scripts/check_groq_tts.py` (new) | Speech-only file-generation diagnostic |
| `scripts/prepare_demo_audio.py` | Optional Groq cloud generation with existing mastering/cache publication |
| `tests/conftest.py` | Isolate tests from a deployed cloud-backend environment setting |
| `tests/test_groq_tts.py` (new) | 18 mocked synthesis, chunking, error, routing, recovery, gain and cache-preservation cases |
| `tests/test_exhibition_demo.py` | Accept prepared cache metadata from either supported neural provider |
| `pytest.ini` | Include Groq test file in discovery |
| `docs/PI_MANUAL_SETUP.md` (new) | Ordered manual Pi deployment/audio/startup instructions |
| `assets/fit_expo/README.md` | Optional Groq regeneration and accurate existing-cache provenance |
| `AURUS_FINAL_IMPLEMENTATION_REPORT.md` | Current integration and verification record |

Verification actually performed:

- Targeted initial Groq/audio regression run: **28 passed, 41 deselected**.
- Final `python -m pytest -q -rs`: **217 passed, 0 failed, 2 skipped**, 6.65 s.
  The two skips are existing OpenCV-unavailable tests. The existing
  pytest-asyncio fixture-loop-scope deprecation warning remains.
- `python -m compileall -q src/services/groq_tts_service.py src/services/tts_service.py scripts/check_groq_tts.py scripts/prepare_demo_audio.py tests/test_groq_tts.py`:
  passed. Both speech scripts' `--help` entry points passed without synthesis.
- Diff whitespace/line-ending verification and source parsing completed after
  documentation edits. Existing neural WAV files and manifest remain unchanged.

All cloud synthesis, playback and driver operations in tests were mocked.
There was no live Groq speech request, no use of the key pasted into chat,
no private `.env` edit, no physical playback, and no GPIO/motor operation.
No commit, push, Pi transfer, service action or deployment was performed.
Live account access, cloud output and actual speaker audibility remain pending
the operator's manual steps. The SDK was already a declared runtime dependency;
no runtime requirement or model binary was added.

Primary references: [Groq Orpheus TTS](https://console.groq.com/docs/text-to-speech/orpheus),
[Groq English model](https://console.groq.com/docs/model/canopylabs/orpheus-v1-english),
[Groq Python speech resource in the supported SDK](https://github.com/groq/groq-python/blob/v0.31.0/src/groq/resources/audio/speech.py).

## R. Original Normal Speech Restored; Live Demo Only — 2026-11-05

The operator requested reversal of the general Groq TTS integration, then
explicitly changed the demo requirement: do not preserve/replay saved recordings;
call Groq with the predefined messages every time. This section supersedes the
normal-provider selection, gain and cached-demo descriptions in O/Q.
The work started from clean `main` at
`93ea062d1e13e45b045b0a8ca4696df60bf7acf0`.

Normal speech restores the pre-integration implementation from `46359db`:
Piper first when its model is available, then Edge/eSpeak/visual fallback as
previously implemented. Ordinary gain and provider-failure recovery are restored.
`AURUS_TTS_BACKEND`, `GROQ_TTS_MODEL` and `GROQ_TTS_VOICE` no longer select normal
speech. Ten normal-speech methods were compared as Python ASTs against that
commit and match: backend selection, health, speak queue, amplification,
synthesis dispatcher, Piper, eSpeak-file, Edge, local WAV playback and routing.
The added general speech model health field was removed.

Only the FIT Expo demo uses the optional Groq adapter, initialized lazily when
the operator starts the demo. Its independent configuration is:

```dotenv
AURUS_DEMO_TTS_MODEL=canopylabs/orpheus-v1-english
AURUS_DEMO_TTS_VOICE=troy
AURUS_DEMO_TTS_GAIN_DB=6
```

It uses the existing private `GROQ_API_KEY`. Each predefined line queues a fresh
cloud synthesis request, including repeated identical lines on later runs.
Lines longer than 200 characters require multiple requests and validated PCM
joining. There is no text-generation call to rewrite the script and no saved
audio lookup or local demo-voice fallback. The reviewed prototype introduction,
Faculty of Information Technology Expo wording, capabilities, hello/greeting
turns and closing remain unchanged. The same short forward/reverse/turn gestures
and all sensor/motor policies are retained; no strafing was added.

The shared serialized audio worker generates a temporary WAV, applies only the
demo's independent peak-limited gain, routes it through Pi/browser/auto, waits
for scoped operator browser acknowledgement when applicable, then deletes the
temporary directory. It keeps microphone playback suppression and busy guards
active during generation/playback. Saved WAVs, preview, manifest, the preparation
script and the ordinary Groq test command were removed. No new cache is written
to the project or reused between requests.

Missing demo configuration rejects a start before entering performing mode.
Cloud/generation/playback failure cancels the demo before later gestures and
keeps its error separate from ordinary speech health/backend. Stop, E-STOP and
owner disconnect continue stopping motion immediately. An in-flight HTTP call
may take its bounded timeout to return; cancellation prevents its result from
playing and prevents subsequent chunks/actions. Already-started local playback
retains its prior bounded behavior and may finish its current clip. A cancelled
or pending audio job keeps subsequent demo starts busy until cleanup completes.

The earlier Pi HTTP 400 output alone did not establish its cause. The adapter
now identifies HTTP 400 and known `model_terms_required`/terms-acceptance replies
without exposing arbitrary SDK response text, user input or keys. Model terms
must be reviewed by the account's appropriate administrator if required; no
terms were accepted automatically. Live demo still requires internet, account
model access and quota. No successful live Orpheus generation has been verified
in this workspace; the operator's previous request was rejected with HTTP 400.

Files modified/deleted in this follow-up:

| File | Change |
| --- | --- |
| `src/services/tts_service.py` | Restore normal speech; independent live demo jobs, routing/acknowledgement, gain and temporary cleanup |
| `src/services/groq_tts_service.py` | Demo-only settings, between-chunk cancellation and actionable sanitized errors |
| `src/services/exhibition_demo.py` | Queue predefined live narration; remove saved-audio validation; cancel on speech failure |
| `src/services/demo_script.py` | Preserve text/order; remove cache validator |
| `src/runtime.py`, `src/web/app.py` | Live-demo composition, acknowledgement and error wording; remove general Groq model telemetry |
| `.env.example` | Replace general cloud TTS selection with demo-only settings; retain unrelated vision correction and ALSA override |
| `tests/conftest.py` | Restore prior hardware-simulation fixture; normal TTS no longer needs cloud-selection isolation |
| `tests/test_exhibition_demo.py`, `tests/test_groq_tts.py` | Replace saved-cache assertions with fresh-request/cleanup/cancel/routing and normal-speech regression coverage |
| `docs/PI_MANUAL_SETUP.md`, `assets/fit_expo/README.md` | Updated normal-speech rollback and live-demo manual setup |
| `assets/fit_expo/*.wav`, `assets/fit_expo/manifest.json` | Remove eight recordings including preview, and their manifest |
| `scripts/prepare_demo_audio.py`, `scripts/check_groq_tts.py` | Remove retired persistent-cache preparation and general cloud speech diagnostic |
| `AURUS_FINAL_IMPLEMENTATION_REPORT.md` | Current behavior and verification record; prior implementation retained as history |

Actual verification:

- Targeted demo/Groq/remaining-sensor suite: **69 passed**, 4.28 s.
- Final `python -m pytest -q -rs`: **218 passed, 0 failed, 2 skipped**, 9.34 s.
  Skips remain the two unavailable-OpenCV cases. The existing pytest-asyncio
  fixture-loop-scope deprecation warning remains.
- Python compilation/source parsing passed. Ten original normal-speech method
  AST comparisons passed against `46359db` after using explicit UTF-8 decoding
  for the verification script; its initial locale-decoded comparison falsely
  differed on a Unicode routing docstring. Production code was unaffected.
- Confirmed no WAV files or manifest remain in `assets/fit_expo`; regression
  tests confirm fresh calls for repeated text, temporary-file removal, HTTP 400
  terms redaction, failure recovery without local demo speech, cancellation
  during an API response, browser ownership/timeout and no movement on failure.
- Final diff/whitespace verification completed after documentation edits.

All automated verification used mocked API/audio calls and simulated hardware.
No live speech request, physical playback, GPIO/motor operation, private `.env`
change, Pi service action, deployment, commit or push was performed. Groq remains
required for the demo and existing AI features, so its original package/key
configuration is retained. See `docs/PI_MANUAL_SETUP.md` for the deliberate Pi
update and `.env` changes; boot startup remains a manual operator action.

## S. Groq Demo Retained, Loudness Enhancement and Confirmation — 2026-11-05

At clean commit `7de3ae32db09118cf0d42a82e692c90f206906ff`, the operator requested
normal-style demo speech plus more audible presentation. During that work the
operator cancelled the provider switch after reporting that Orpheus terms had
been accepted. The pending provider-switch changes were removed. Section R's
live Groq/Troy demo, original normal speech, fixed messages, fresh requests,
temporary playback, cancellation and motion behavior remain in place.

The independently requested demo loudness improvement is retained:

```dotenv
AURUS_DEMO_TTS_MODEL=canopylabs/orpheus-v1-english
AURUS_DEMO_TTS_VOICE=troy
AURUS_DEMO_AUDIO_ENHANCE=true
AURUS_DEMO_TTS_GAIN_DB=6
```

`src/services/speech_audio.py` uses the existing NumPy dependency to remove DC,
reduce low bass, add a broad 3 dB presence lift near 2.4 kHz, compress the RMS
envelope at 3:1 with a soft knee, and apply bounded make-up gain. A short
look-ahead prevents leading transients from forcing excessive whole-clip
attenuation. Final sample peaks stay below -1 dBFS. Channel count, sample rate
and duration are preserved; silence stays silent. This replaces only the demo's
former gain-only processing when enhancement is enabled. False retains the
previous bounded gain helper. Normal speech gain/provider behavior is unchanged.
Physical room loudness and true inter-sample peaks have not been measured.

`scripts/check_demo_tts.py` loads the private project `.env`, synthesizes the
actual multi-chunk introduction, applies the same demo enhancement, reports
model/voice/duration and removes the temporary audio. The default command does
not play anything; `--play --device plughw:2,0` additionally invokes ALSA for the
operator's confirmed headphone output. No robot runtime/GPIO/motors are started
and no key is printed. This gives the operator one concrete live confirmation
after transferring the new files. Accepted terms were reported by the operator;
successful live synthesis/audible playback remain pending that Pi command.

Files changed: `.env.example`, `src/services/tts_service.py`, new
`src/services/speech_audio.py`, new `scripts/check_demo_tts.py`,
`tests/test_groq_tts.py`, `docs/PI_MANUAL_SETUP.md`, `assets/fit_expo/README.md`
and this report. Provider-switch changes to runtime, demo controller and web
adapter were restored to the pre-turn commit; no cancelled backend switch is
left in the implementation.

Actual verification:

- The first synthetic loudness regression found that compressor attack
  transients reduced average level after peak limiting. Added 20 ms look-ahead;
  the same average-level assertion then passed without weakening its threshold.
- Targeted Groq/demo tests: **48 passed**, 2.15 s, before adding two CLI cases.
- Final `python -m pytest -q -rs`: **225 passed, 0 failed, 2 skipped**, 7.39 s.
  Skips remain the existing OpenCV-unavailable cases. The existing pytest-asyncio
  fixture-loop-scope deprecation warning remains.
- Added synthetic checks for average level over the prior bounded-gain method,
  presence relative to bass, preserved mono/stereo format/duration/balance,
  silence, peak headroom and CLI success/failure/temporary cleanup. Cloud,
  playback and hardware calls were mocked. Source parsing/compilation, CLI help
  and final diff checks passed.

No live API request, physical playback, GPIO/motor operation, private `.env`
change, deployment, service action, commit or push was performed. No runtime
dependency was added. The provided Pi test is the remaining live confirmation.

## T. Dashboard Camera ON/OFF — 2026-11-05

The operator approved a simple Camera ON/OFF control to pause unnecessary
capture and face processing during the expo. This follow-up started from clean
`main` at `6be17422fdb195ec7ab63440d23a8c3c9c8f29e2` with no pre-existing
uncommitted changes. It adds no separate face-detection-only switch.

The Visual identity panel has a Turn camera off/on button. OFF clears the live
image, current identity and recognition candidates, cancels unfinished
enrollment, and requests hardware release. The existing single camera worker
alone performs camera open/read/close operations. OFF waits without capture or
model processing; ON wakes the same worker. Rapid OFF/ON changes invalidate the
old session and close its device before opening another. A blocked driver read
shows Stopping rather than falsely claiming release. Release errors remain
visible and are retried; camera ownership is retained until close succeeds.

The default initial state is configured by:

```dotenv
AURUS_CAMERA_ENABLED=true
```

True preserves previous startup behavior. False skips initial model loading and
capture until explicitly enabled. Dashboard changes last until restart; no
private `.env` was modified automatically. Saved identities, face embeddings
and memories are not deleted. Enrollment requires a healthy, fresh image and
its start is serialized with the camera control request.

Snapshots and control telemetry carry enabled/session-generation fields. A
read or processing/error publication from an older session cannot overwrite
OFF or a newly enabled session. Existing vision questions, MCP capture_image,
exploration descriptions and face greetings reject late results across
OFF/ON. Already delivered images/messages are not retroactively removed from
historical discovery logs or completed conversations. No new cloud request is
required for changing camera state.

OFF during Explore halts the arbiter first, ends the mission and returns to
idle. ON never resumes the mission automatically. The arbiter rejects late
explore-forward and curiosity-scan commands outside Explore so an in-flight
scan cannot undo that pause. Other manual/MCP movement, ultrasonic collision
policy, E-STOP, normal speech and Groq demo narration/gestures are unchanged.

SocketIO validates actual booleans and broadcasts the control state to all
dashboards. Disabled is distinct from a camera fault. The UI removes the old
stream URL and identity, displays OFF/starting/stopping/error status, disables
enrollment/memory/Explore controls when appropriate, and reconnects a new stream
only for a fresh enabled session. Stale telemetry cannot restore an old image,
stream session or recognized visitor. Disconnect hides the browser's old view
and disables camera controls until it reconnects.

Changed/new source and documentation:

| File | Change |
| --- | --- |
| `src/services/vision_service.py` | Desired state, one worker, interruptible waits, owner-only release/retry, frame invalidation and serialized identity writes |
| `src/core/models.py` | Enabled/generation vision fields and same-session result validation |
| `src/runtime.py` | Startup setting, camera control, Explore pause/guard, enrollment checks, fresh visual response/greeting validation and health |
| `src/services/behavior_controller.py` | Camera-off exploration stop and stale greeting/observation suppression |
| `src/services/mcp_agent_service.py` | Camera-off feedback and same-session capture results |
| `src/services/motion_arbiter.py` | Reject late Explore-only movement after a mode change |
| `src/web/app.py` | Camera state on connect, strict boolean event and shared dashboard updates |
| `src/web/templates/index.html`, `src/web/static/css/evaluation.css`, `src/web/static/js/evaluation.js` | Switch, no-camera overlay, status styling, fresh stream/identity handling and disabled controls |
| `.env.example` | Default camera startup switch |
| `tests/test_camera_control.py` (new), `pytest.ini` | 21 simulated lifecycle, cloud, mode, identity and multi-dashboard cases; discovery |
| `docs/PI_MANUAL_SETUP.md`, `AURUS_FINAL_IMPLEMENTATION_REPORT.md` | Manual Pi rollout/checks and this verification record |

Actual verification:

- The initial existing suite found one simulation-only exploration harness
  without a vision service. Preserved that optional legacy/test caller while
  enforcing the camera-off guard on the real runtime's vision service.
- The first new camera suite passed its behavior assertions but failed cleanup
  because the fake demo omitted owner_disconnected. Corrected the test fake;
  no production disconnect behavior was weakened.
- Targeted camera/remaining-sensor suite: **46 passed**, 2.68 s.
- Final `python -m pytest -q -rs`: **246 passed, 0 failed, 2 skipped**, 10.68 s.
  Skips remain the existing OpenCV-unavailable cases. The existing
  pytest-asyncio fixture-loop-scope deprecation warning remains.
- Isolated headless Edge exercised camera ON/OFF requests, pending controls,
  start/stop state, stream removal/reconnection, disabled identity/Explore
  actions, stale health/identity telemetry and disconnect/reconnect. Browser
  **20 assertions** and visual OFF-state review passed, including a restarted
  server with a reset generation counter; no real server/hardware was
  connected. Source compilation, JavaScript syntax and final diff checks passed.

No real camera, microphone, speaker, GPIO/motor or live cloud API was operated.
No Pi deployment, service change, private key/`.env` edit, commit or push was
performed. Hardware driver release/reconnection, actual CPU/temperature benefit
and physical Explore stopping remain Pi/operator checks. See the manual setup
guide's camera section after deliberately transferring the reviewed changes.
