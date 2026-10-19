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

> **The physical CTS600 control panel and this integration through the gateway cannot operate at the same time.** The integration emulates and replaces the panel. Disconnect the original panel from the RS485 bus before starting the integration. Turning off the control unlock helper does not stop panel emulation; disable the integration before reconnecting the physical panel.

The reference gateway for this integration is the **Waveshare RS232/485/422 TO POE ETH (B)**. See the [manufacturer's documentation](https://www.waveshare.com/wiki/RS232/485/422_TO_POE_ETH_(B)) for wiring and gateway configuration.

### 1. Prepare the installation

You need the Waveshare gateway, an Ethernet connection, PoE or a separate power supply supported by the gateway, a twisted pair for RS485, and a working Home Assistant room temperature sensor to replace the panel's T15 sensor. Keep the original panel available for manual restoration.

Before disconnecting anything, record the unit's operating mode, temperatures, ventilation settings and schedule. Photograph the existing panel wiring and obtain the wiring diagram for your exact unit and control-board revision.

### 2. Connect the gateway to the CTS600 control board

Disconnect the Nilan unit from its mains supply before opening the electrical compartment or changing wiring. The compartment contains mains-voltage circuits; have a qualified installer perform work that requires electrical competence.

1. Locate the original CTS600 panel connection on the main control board, identified as **CN6 in this installation**. Verify the connector and each signal against the unit's wiring diagram and board markings. Other controller families and board revisions can use different connectors.
2. Identify the two RS485 communication conductors and the panel power conductors. Do not identify them by wire color alone. This README does not provide a verified numbered CN6 pinout for every VP18 board; do not infer pin numbers or connector orientation from the table below.
3. Disconnect the original panel from the communication bus. This integration takes its place as the panel; do not operate both in parallel.
4. Configure the Waveshare hardware for **two-wire RS485** using the interface selection and wiring instructions for your gateway revision. Connect the communication pair as follows:

| CTS600 panel connection on the main board | Waveshare connection |
| --- | --- |
| Verified RS485 A signal | RS485 A / positive data terminal, as identified in the gateway's RS485 wiring diagram |
| Verified RS485 B signal | RS485 B / negative data terminal, as identified in the gateway's RS485 wiring diagram |
| Panel supply +12 V | Leave disconnected from the gateway and individually insulate the unused conductor |
| Panel supply return / 0 V | Leave disconnected in this two-wire installation and individually insulate the unused conductor |

Use the gateway's **RS485** terminal assignments, not its RS232 pins or the separate RS422 receive pair. A/B and +/- naming can differ between manufacturers: check both diagrams before connecting. Do not add RS422 terminal bridges unless the gateway's instructions explicitly require them for RS485 mode.

5. Secure the twisted pair and provide strain relief. Keep the low-voltage wiring separated from mains wiring. Apply termination and shielding according to the unit's and gateway's instructions for the actual cable run; do not add resistors or ground connections by guesswork.
6. Power the gateway from **PoE or its own suitable external supply** and connect Ethernet to the Home Assistant network. The panel's 12 V output is not used to power the gateway in this reference installation. Never connect panel power to RS485 data terminals.
7. Check the wiring, close the electrical compartment and restore power to the Nilan unit.

The two-wire panel-replacement approach is also described in the [original integration's physical connection guide](https://github.com/frodef/nilan-cts600-homeassistant#physical-connection). Its photographs refer to the original author's unit, not a verified pinout for your board.

### 3. Configure the Waveshare gateway

Use the gateway's web interface or the manufacturer's configuration utility to set a reachable LAN address. Reserve that address in DHCP or configure a suitable static address. Set these parameters, save them and restart the gateway if required:

| Setting | Value for this integration |
| --- | --- |
| Serial interface | Two-wire RS485 |
| Baud rate | 19200 |
| Data bits | 8 |
| Parity | None |
| Stop bits | 2 |
| Network mode | TCP Server; Home Assistant connects as the client |
| TCP listening port | 4196, or another chosen port also entered in Home Assistant |
| Conversion protocol | NONE / transparent transmission |
| Multi-host / multiple control clients | Disabled; use Home Assistant as the sole control client |

Do not select Modbus TCP-to-RTU conversion. Disable any configured registration packets, application heartbeat payloads or automatic polling that would inject extra bytes into the serial stream. Close serial test tools before starting the integration. No virtual COM port driver is needed on the Home Assistant host.

### 4. Install and configure Home Assistant

Install the integration using the [HACS instructions above](#installation-with-hacs), then restart Home Assistant. Under **Settings → Devices & services → Helpers**, create a **Toggle** helper for control permission and leave it off for initial setup. Make sure your room temperature sensor is available and reporting a fresh value before adding the integration.

Open **Settings → Devices & services → Add integration → Nilan CTS600**, enter the gateway address and listening port, select your T15 sensor and control helper, and confirm the panel disconnection only after completing the wiring steps above. The following notes explain the setup behavior and sensor requirements.

The integration replaces the CTS600 control panel and uses its communication protocol. It is not a standard CTS602 Modbus TCP integration. Use transparent TCP transport and configure the gateway for **19200 baud, 8N2, Protocol NONE**. Setup requires confirmation that the original panel has been disconnected and the CN6 wiring has been checked.

Creating a configuration starts panel emulation, sends the T15 room temperature and switches the menu language to English. Turning off the control unlock helper blocks user control commands, but does not stop communication or panel emulation.

In the setup form, select:

- The gateway IP address or hostname and TCP port. The default port is 4196; use the port configured on your gateway.
- A primary and, optionally, a backup T15 room temperature sensor. Each must provide a fresh numeric temperature in °C within the range 5–40 °C.
- An existing Toggle helper (`input_boolean`) to unlock controls. Create one under **Settings → Devices & services → Helpers** if needed.

The room sensor entities prefilled in the form come from the author's installation. Replace them with your own sensors when setting up a new installation.

The default maximum T15 reading age is 15 minutes. If `input_number.nilan_t15_max_age_minutes` exists, its value is used within the range 1–60 minutes. Missing or invalid temperature readings block control commands.

### 5. Verify operation

1. Check that the integration connects and that the display/status and temperature entities update. Confirm that T15 follows the selected room sensor and that reported temperatures are plausible.
2. Review the unit's alarms and the Home Assistant logs. A successful TCP connection alone does not prove that the RS485 wiring or panel protocol is working.
3. Turn on the control unlock helper when ready. Test a small, reversible change to fan speed or target temperature and verify the result from the reported unit state. Restore the desired settings afterward.
4. Check ventilation, operating modes and both DHW controls against the functions supported by your unit. Test backup T15 selection and connection recovery under supervision before relying on unattended operation.

If TCP cannot connect, check the gateway address, listening port, LAN routing and whether another client holds the connection. If TCP connects but CTS600 data does not update, check two-wire RS485 mode, the verified A/B assignments, 19200 8N2, transparent protocol and physical panel disconnection. Power down before changing wiring.

To restore the physical panel, first disable the integration so it stops emulating the panel, isolate the unit's power, disconnect the gateway's communication pair and reconnect the original panel exactly as documented before installation. Close the compartment, restore power and verify normal panel operation. Turning off the control unlock helper alone does not stop panel emulation.

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
