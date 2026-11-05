# AURUS 3.0 — Final Implementation and Recovery Report

Date: 2026-11-04 (Asia/Colombo). Implementation and simulation tests only.

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
