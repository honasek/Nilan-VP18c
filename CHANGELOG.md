# Changelog

## 2026.10.25.0

- Ship translations/en.json with the full contents of strings.json so users without a dedicated translation receive English titles, field labels and error messages.
- Validate that the English runtime translation exists and matches strings.json before publishing a release.
- Address the English fallback review feedback in hacs/default#12584. No device-control behavior changes.

## 2026.10.24.0

- License the repository and installed integration under the standard MIT License, permitting commercial use and redistribution.
- Preserve frodef's original public-domain Unlicense and attribution in the repository and HACS-installed integration.
- Clarify the licensing terms in the README. No device-control behavior changes in this update.

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
