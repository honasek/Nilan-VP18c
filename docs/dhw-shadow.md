# DHW shadow simulation

The shadow simulator predicts conditional trajectories. It does not send commands, meter draw volume, establish calibrated probabilities or enable live control. Its version is published as `simulation_version: 3`; the persisted learner remains version 1 so existing evidence survives this update.

## Demand trajectories

Use up to the latest 14 sufficiently covered **past** days. A day needs at least 18 hours of coverage, 135 minutes during 06:00–09:00 and 225 during 18:00–23:00. At least three such days are required to use historical demand.

For the expected trajectory, select an actual day closest to the median modeled daily volume. L1 distance to other daily trajectories resolves ties. This preserves the order and timing of one day's draws instead of combining independent half-hour estimates. For the stress trajectory, select the historical day with the greatest total draw over the actual forecast horizon and multiply its **entire** trajectory by 1.2. These scenarios are not calibrated probabilities or separately metered consumption.

Normalize each covered half-hour's liters by observed minutes. Retain an observed zero. Where coverage is less than 20 minutes, fill that bin from the configured daily prior. If fewer than three eligible days exist, use the explicit prior for the entire expected profile and 1.2 times that prior for stress. The configured daily liters are a **fallback**, not a floor imposed on every observed half-hour. Local-day bins account for elapsed time across daylight saving clock changes.

Historical daily patterns are repeated if a horizon crosses a calendar day. They cannot establish future weekend, occupancy or seasonal demand that has not yet been observed.

## Readiness and subsequent comfort

All scenarios start from the same frozen thermal ensemble and run to the same endpoint. A night plan includes the following morning through 09:00; a day plan includes the evening through 23:00. If the configured deadline is later, it is the endpoint. Heating follows the same assumed thermostat rules after the deadline; the deadline is not a stop-heating command.

Report these separately:

- `finish_at`: first target crossing, conservatively summarized across thermal paths; null if enough paths do not reach it within the horizon.
- `target_reached_with_margin`: the completion of the currently valid cycle occurs by the deadline minus the configured time margin.
- `ready_at_deadline`: T12 meets the nominal target with the existing half-degree telemetry tolerance, or an observed/simulated completed cycle remains inside the supported hysteresis band.
- `target_held_until_deadline`: after completion, T12 stays above the conservative restart-band edge until the deadline. `nominal_target_held_until_deadline` separately reports continuous holding of the nominal target with its half-degree telemetry tolerance.
- `post_use_comfort_met`: the configured upper T11 and modeled-stock minima are maintained from the deadline through the common endpoint.

Scan the whole candidate window on a five-minute grid, including exact first/last endpoints. Do not assume feasibility is monotonic: an early crossing followed by cooling can fail while a later start passes. Publish only a tested passing candidate. A grid is not a continuous-time optimum. A candidate must meet waiting reserves, deadline timing/readiness, post-deadline comfort and cancellation guards for **both** expected and stress trajectories. Continuous target holding remains a separately displayed diagnostic; deadline readiness is checked even if an earlier crossing occurred.

The existing live-control boundary additionally checks both profile passes and post-use comfort for plans with simulation version 2 or later. Learned-data requirements, manual permissions, original-target recovery and explicit live enablement remain in place. No switch is enabled by installing this version.

## Energy comparisons

Run every selected thermal path to the common endpoint even if it reached the target earlier or never reached it. Count modeled electricity for the whole compressor, including the preserved currently observed house requirement. Report DHW-request electricity separately because it can overlap with that house requirement. The compressor input is the existing 450 W assumption; this is not a meter or a tariff calculation.

Report remaining tank heat relative to each path's modeled inlet temperature, final layers and modeled stock. Compare the same thermal paths between a candidate and the normal-regulation baseline; this avoids subtracting unrelated ensemble medians. `compressor_delta_kwh` is candidate minus baseline; `end_tank_heat_delta_kwh` reports the corresponding difference of stored thermal energy. The two are different physical quantities and are never subtracted as though they were both electrical consumption.

An electrical saving is publishable only if both scenarios satisfy the readiness/comfort conditions, have the same horizon, and **every** paired terminal profile differs by at most 0.05 kWh of stored heat and 0.5 °C in every layer. Otherwise `saving_electric_kwh_model` is null and the reason identifies differing terminal state or unmet service. A cooler or differently stratified tank cannot be credited as a saving. Monetary savings remain null; no unverified COP is used to invent a conversion cost.

## Persistence and limits

Stores and learned coefficients are preserved. Archived plans include both frozen day profiles, thermal initial conditions, candidate checks and common endpoint. Large frozen input arrays are excluded from public entity attributes. Existing Recorder exclusions and entity IDs remain unchanged.

The existing assumptions remain material: unknown real draw, unverified probe positions, conditional compressor start, passively inferred hysteresis (an explicitly unverified 3 °C fallback until evidence exists) and preservation of the currently observed house-heating/cooling requirement. Restoring a target does not prove the controller will immediately start its compressor. Completing 14 days of collection alone does not validate future comfort or timing accuracy.

## Reference and hysteresis (simulation v3)

Reserve percentages are normalized to the current compressor target above 30 °C. Thermostat evidence is learned passively from real clean on/off transitions and is stored separately from thermal parameters. Expected paths use the median restart drop; stress paths use its wider uncertainty edge. Deadline readiness distinguishes a nominal crossing from a completed cycle still inside the supported hold band. Initial completion must be supported by actual history, not merely an idle compressor. Shared horizons, terminal-state comparisons, coherent whole days and the exhaustive five-minute candidate scan remain in place.
