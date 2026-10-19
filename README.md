# Nilan VP18c / CTS600 for Home Assistant

[![Validate](https://github.com/honasek/Nilan-VP18c/actions/workflows/validate.yml/badge.svg)](https://github.com/honasek/Nilan-VP18c/actions/workflows/validate.yml)

A custom integration for the **Nilan CTS600** controller connected through a transparent TCP–RS485 gateway. This variant is based on [frodef/nilan-cts600-homeassistant](https://github.com/frodef/nilan-cts600-homeassistant) and includes extensions developed and used with a Nilan VP18 K.

Current distribution version: **2026.10.19.3**. The runtime code matches version 2026.10.19.2, tested on Home Assistant Core **2026.10.2**; release .3 updates packaging and metadata for HACS. Support for individual menu settings depends on the unit and its firmware. The repository name does not imply that every VP18 variant has been tested.

## Features

- Climate entity with operating mode, target temperature and fan speed.
- Economy, Comfort and More hot water presets, where supported by the unit's menu.
- CTS600 temperatures, display, operating status and alarms.
- Controls for supported user and service menu settings, the clock and the weekly schedule.
- Separate domestic hot water (DHW) controls: compressor using T12 and electric heater using T11.
- Local filter maintenance tracking with the date of the last service.
- Estimated DHW thermal reserve, short-term forecasts and passive planning simulation.
- Primary and backup T15 room temperature sensors, temperature validation and a separate control unlock helper.

The DHW model provides estimates based on sensor readings and learned behavior. The passive planning simulation does not operate the unit. The author's personal automations, energy planner and dashboards are outside the scope of this integration.

## Installation with HACS

1. In HACS, open **⋮ → Custom repositories**.
2. Add `https://github.com/honasek/Nilan-VP18c` with the **Integration** category.
3. Download **Nilan VP18c / CTS600** and restart Home Assistant.
4. Prepare a room temperature sensor and a control unlock helper as described below.
5. Open **Settings → Devices & services → Add integration → Nilan CTS600**.

The repository can be added as a custom repository. It is not currently listed in the default HACS catalog.

Select a published version in the download dialog. HACS installs files from `custom_components/nilan_cts600` in the corresponding GitHub release; no manual extraction or extra nested folder is needed. See [Releases](https://github.com/honasek/Nilan-VP18c/releases) for available versions.

For manual installation, copy `custom_components/nilan_cts600` to the same path in your Home Assistant configuration directory and restart Home Assistant. The integration domain is `nilan_cts600`, so this variant replaces any existing integration using that domain.

## Connection and initial setup

The integration replaces the CTS600 control panel and uses its communication protocol. It is not a standard CTS602 Modbus TCP integration. Use transparent TCP transport and configure the gateway for **19200 baud, 8N2, Protocol NONE**. Setup requires confirmation that the original panel has been disconnected and the CN6 wiring has been checked.

Creating a configuration starts panel emulation, sends the T15 room temperature and switches the menu language to English. Turning off the control unlock helper blocks user control commands, but does not stop communication or panel emulation.

In the setup form, select:

- The gateway IP address or hostname and TCP port. The default port is 4196; use the port configured on your gateway.
- A primary and, optionally, a backup T15 room temperature sensor. Each must provide a fresh numeric temperature in °C within the range 5–40 °C.
- An existing Toggle helper (`input_boolean`) to unlock controls. Create one under **Settings → Devices & services → Helpers** if needed.

The room sensor entities prefilled in the form come from the author's installation. Replace them with your own sensors when setting up a new installation.

The default maximum T15 reading age is 15 minutes. If `input_number.nilan_t15_max_age_minutes` exists, its value is used within the range 1–60 minutes. Missing or invalid temperature readings block control commands.

## Services and stored data

The `set_clock`, `set_weekly_slot`, `discover_menu_options` and `acknowledge_alarm` actions are available under the `nilan_cts600` domain. Field descriptions are provided in [services.yaml](custom_components/nilan_cts600/services.yaml).

Menu changes use serialized operations and are verified by reading the value back. Individual settings may be restricted by the firmware or require service access.

The filter maintenance date, DHW model and learned data are saved in Home Assistant storage. Saving a maintenance date does not send a command to the unit or acknowledge its alarm. Keep this stored data when updating the integration.

The model and simulation may use helpers and measurement entities from a particular home configuration. This repository does not include personal `configuration.yaml` files, packages, measurement history or Lovelace dashboards. These require your own entity configuration and are not created by HACS installation.

## Development and validation

The repository follows the HACS **Integration** directory layout:

```text
hacs.json
README.md
LICENSE
custom_components/
  nilan_cts600/
    manifest.json
    __init__.py
    brand/
    translations/
    ...other modules and services.yaml...
docs/             # historical documentation; not installed in HA
script/           # repository validation
tests/            # tests; not installed in HA
.github/workflows/validate.yml
```

`content_in_root` and `zip_release` are disabled. HACS uses the standard integration directory in the repository without a separate installation ZIP asset.

```sh
python -m pip install -r requirements-test.txt
python script/validate_repository.py
python -m pytest tests -q
python -m compileall -q custom_components
```

The included portable tests validate the thermal model without connecting to a physical unit. They do not provide complete coverage of hardware behavior or every control path.

Every push and pull request runs the official **HACS Action**, **Home Assistant Hassfest**, repository validation and model tests. All checks must pass before a release, and the release tag must match `version` in `manifest.json`. See [CHANGELOG.md](CHANGELOG.md) for release history.

## Credits and license

Original protocol and integration author: **frodef**. Original project: [nilan-cts600-homeassistant](https://github.com/frodef/nilan-cts600-homeassistant).

The original **Unlicense** is preserved in [LICENSE](LICENSE). The historical [UPSTREAM_README.md](docs/UPSTREAM_README.md) is included for attribution and background. Its instructions describe the original variant; use the instructions above for this TCP variant.
