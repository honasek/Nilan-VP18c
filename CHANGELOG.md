# Changelog

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
