# Changelog

## 2026.10.25.1

- Normalize public thermal reserve against the current compressor DHW setpoint above 30 °C; refresh immediately on setpoint changes while preserving physical profiles and learned parameters.
- Passively observe clean compressor starts/stops, exclude rapid draw crossings, gaps, target changes and controlled leases, and persist thermostat evidence separately from physical learning. Bootstrap from actual retained samples.
- Report measured or explicitly transferred hysteresis with integer-probe uncertainty and per-target evidence counts. A confirmed completed cycle remains reached while cooling within the supported band; compressor-off alone does not imply completion.
- Apply the same reference and observed hysteresis to shadow readiness, reserve and expected/stress scenarios; retain common horizons and whole-window candidate search.
- Show the active reference and passive hysteresis evidence on the existing DHW model dashboard. Add device-free regression coverage; no new entities or Recorder changes.

## 2026.10.24.1

- Evaluate every scenario through one common horizon, including demand after the readiness deadline: through 09:00 for the night window and 23:00 for the daytime window, or the deadline if later.
- Compare paired terminal tank energy and stratification alongside compressor electricity. Suppress savings when terminal states or delivered service are not comparable.
- Separate a representative whole historical day from a coherent high-demand day scaled by 1.2. Stop stacking unrelated half-hour peaks; retain the explicit daily prior for insufficient history and uncovered bins.
- Scan the entire candidate window every five minutes even when the earliest start fails. Distinguish first target crossing, deadline readiness, continuous holding and subsequent comfort.
- Require both demand profiles and post-deadline comfort for new live-control plans. Preserve learned stores, permissions, default-off controls and existing entity IDs.
- Add portable regression tests, including a real thermal case where an early start fails but a later start succeeds. Validate the adapter and scheduler against HA Core 2026.10.3.

## 2026.10.23.1

- Preserve the deployed polling resilience: an unavailable optional SHOW DATA menu does not invalidate the basic poll; its error remains visible.
- Retain cached telemetry for only the first consecutive transport failure and reconnect on the next poll. Persistent failures still become unavailable.

## 2026.10.21.2

- Refresh and verify each main-screen edit field before changing the room target or fan level; do not trust the preceding ENTER response or pre-edit value.
- Step from the current confirmed value, verify each individual step and confirm the saved value after leaving edit mode.
- Bound delayed-frame refreshes to three no-key requests; never replay uncertain steps or commits. Preserve the coordinator's independent readback and command guards.
- Add synthetic-panel tests for delayed and changed snapshots, wrong fields, limits and uncertain writes.

## 2026.10.21.1

- Wait for both T11/T12 readings before updating the DHW model or refreshing forecasts; skip incomplete samples without a traceback.
- Preserve learned state and mark reserve, forecasts and shadow plans unavailable while waiting; resume automatically when complete readings return.
- Recheck input availability when scheduled work starts and before publishing results. Keep strict physical validation and unexpected-error diagnostics.
- Add a device-free native HA regression check covering missing inputs, recovery and scheduling races.

## 2026.10.20.1

- Mark the live DHW scheduler as a Home Assistant callback so timer and state-change handlers run on the event loop.
- Fix repeated thread-safety exceptions and unawaited `TUVLiveRuntime._tick` coroutine warnings, including when live control is disabled.
- Add a device-free regression check using real Home Assistant timer, state-change and coordinator dispatch.
- Preserve live-control permissions, default-off behavior, target recovery, settings and existing entity IDs.

## 2026.10.19.4

- Add a separate opt-in live DHW delay switch, requiring existing control permissions and shadow collection.
- Apply only learned, fresh full-delay scenarios with reserve and deadline checks, using the existing compressor target and verified serialized commands.
- Persist original-target ownership before writes; restore on cancellation, expiry, restart and disable while preserving manual overrides.
- Keep live control off after reload/restart and expose pending recovery/errors in switch attributes.
- Recompute shadow plans every minute while live control is armed and preserve the original target for forecasts.
- Add policy, persistence, failure and recovery tests.

## 2026.10.19.3

- Prepare the repository for HACS installation and versioned GitHub releases.
- Keep exactly one integration in `custom_components/nilan_cts600`.
- Declare the standard HACS directory layout and the Home Assistant integration type.
- Express the shared pymodbus requirement as a minimum supported version, as required by Hassfest for packages managed by Home Assistant.
- Move the historical upstream guide to `docs/`; retain the original license and attribution.
- Add HACS, Hassfest, repository-layout and thermal-model validation on pushes and pull requests.
- Preserve the deployed integration's runtime code and entity domain.

## 2026.10.19.2

- Import the deployed custom CTS600 integration, including climate presets, separate DHW controls, filter maintenance and the thermal model.
