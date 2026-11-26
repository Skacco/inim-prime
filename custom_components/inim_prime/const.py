from typing import Final

import inim.prime.native.const

DOMAIN: Final[str] = "inim_prime"


PANEL_LOG_EVENTS_FETCH_LIMIT_DEFAULT: Final[int] = 30
PANEL_LOG_EVENTS_FETCH_LIMIT_TRIGGER: Final[int] = 3
PANEL_LOG_EVENTS_FETCH_LIMIT_MIN: Final[int] = 10
PANEL_LOG_EVENTS_FETCH_LIMIT_MAX: Final[int] = 100

# After a failed reconnect, wait this long before the next attempt (seconds).
NATIVE_RECONNECT_MIN_INTERVAL = 1.0
# Once the panel has been unreachable for NATIVE_RECONNECT_FAST_WINDOW the
# outage is not a short drop: reconnect attempts slow down to this interval.
NATIVE_RECONNECT_FAST_WINDOW = 30.0
NATIVE_RECONNECT_SLOW_INTERVAL = 5.0
# A command issued while the panel is unreachable waits this long for the
# connection to come back before giving up (seconds).
NATIVE_COMMAND_RETRY_WINDOW = 15.0
# On a reload (options saved, Reconfigure) the Native connection is closed
# and opened again: the panel is given this long to release the old one.
NATIVE_REOPEN_DELAY = 1.0
# A new connection closed without an answer this soon after our own close is
# the panel still releasing the old one, not a refused password: retried.
NATIVE_REOPEN_GRACE = 30.0
# Same for PrimeLAN: a command whose connection cannot even be opened (the
# panel resets new connections for a few seconds during a drop) is retried
# for this long (seconds).
PRIMELAN_COMMAND_RETRY_WINDOW = 15.0
PRIMELAN_COMMAND_RETRY_INTERVAL = 1.0
# Entities keep their last state for this long when the panel stops answering,
# so a short drop does not flip everything to "unavailable" (seconds).
CONNECTION_GRACE_PERIOD = 30.0
# Inim panels were seen closing the local connection for ~10 s on a fixed
# wall-clock cadence of 2^32 microseconds (~71.6 min), at the same instant on
# unrelated installations. A capture showed the panel closing all its
# connections at that instant, its own link to the Inim servers included,
# and reopening them ~10 s later: it restarts its network side by itself.
# Only used to label the drop in the log.
PERIODIC_DROP_PERIOD = 4294.967296
PERIODIC_DROP_PHASE = 2147.483648
PERIODIC_DROP_TOLERANCE = 5.0
# The panel restarts its network at those instants only when a local
# connection is open then: with Native, the connection is closed shortly
# before each instant and reopened shortly after (seconds). Measured on two
# Prime panels: closing up to 3s ahead and reopening from 0.5s after was clean.
# Closed exactly this long before the instant, on any panel (clean cycles
# were seen closing 0.6-1.2s ahead).
NATIVE_CYCLE_PAUSE_BEFORE = 0.7
# Reopened this long after. Now and then the panel restarts its network right
# after the reopening wherever it is (drops seen starting 0.54s, 0.83s and
# 1.03s past the instant with 0.5s, 0.8s and 1.0s, about as often): a later
# reopening does not avoid it, so the shortest pause is kept.
NATIVE_CYCLE_PAUSE_AFTER = 0.5
# The connection is closed exactly the configured time before the instant,
# whatever the size of the installation. For that no new request is sent
# from a little earlier: the longest request measured on this panel, times
# this factor plus this margin (seconds), within these bounds.
NATIVE_CYCLE_PAUSE_LEAD_FACTOR = 1.2
NATIVE_CYCLE_PAUSE_LEAD_MARGIN = 0.1
NATIVE_CYCLE_PAUSE_LEAD_MIN = 0.2
NATIVE_CYCLE_PAUSE_LEAD_MAX = 3.0
# True: the connection is closed as soon as the request in flight has ended,
# so anywhere between the moment the requests stop and the configured time
# (the configured time is then the latest it can be closed at). False: it
# stays open and idle until the configured time and is closed exactly then.
NATIVE_CYCLE_PAUSE_CLOSE_WHEN_FREE = False
# The lead is taken from this share of the measured requests: a rare slow one
# does not lengthen every pause (it is waited for, and reported, when it
# happens to be running at the planned time).
NATIVE_CYCLE_PAUSE_LEAD_PERCENTILE = 0.98
# Used until enough requests have been measured.
NATIVE_CYCLE_PAUSE_LEAD_DEFAULT = 0.6
# Requests whose duration is remembered for the measure (a few minutes' worth).
NATIVE_REQUEST_DURATIONS_KEPT = 2000
# PrimeLAN requests whose duration is remembered for its pause's lead
# (they are fewer than Native's: a few every ten seconds).
PRIMELAN_REQUEST_DURATIONS_KEPT = 500
# Upper bound to a PrimeLAN command's expected duration (seconds): the
# client's own timeout is longer; before anything is measured, this.
PRIMELAN_EXPECTED_MAX = 10.0

# Native defaults
NATIVE_PORT_DEFAULT: Final[int] = inim.prime.native.const.Panel.DEFAULT_PORT
NATIVE_USE_OUTER_FRAME_DEFAULT: Final[bool] = False
NATIVE_PASSWORD_DEFAULT: Final[str] = inim.prime.native.const.Panel.DEFAULT_PASSWORD
# Optional check, in the connection form, that the Native port cannot be reached from the
# Internet. Off by default: it asks an external service to try the port on the
# public address this request comes from (any server running "echoip").
NATIVE_CHECK_PORT_EXPOSED_DEFAULT: Final[bool] = False
NATIVE_PORT_CHECK_SERVER_DEFAULT: Final[str] = "https://ifconfig.co"
NATIVE_USE_CUSTOM_PIN_DEFAULT: Final[bool] = False
NATIVE_PIN_MAX_CHARACTERS: Final[int] = inim.prime.native.const.Panel.MAX_PIN_CHARACTERS

# Primelan defaults
PRIMELAN_USE_HTTPS_DEFAULT: Final[bool] = True

# --- Defaults (custom per coordinator) ---
ZONES_SCAN_INTERVAL_NATIVE_DEFAULT: Final[int] = 500
PARTITIONS_SCAN_INTERVAL_NATIVE_DEFAULT: Final[int] = 500
# SmartLiving: zones and areas share the same reads; the board wants >= 0.5 s.
SMARTLIVING_SCAN_INTERVAL_DEFAULT: Final[int] = 1000
SMARTLIVING_DEFAULT_PORT: Final[int] = 5004

ZONES_SCAN_INTERVAL_PRIMELAN_DEFAULT: Final[int] = 5000
PARTITIONS_SCAN_INTERVAL_PRIMELAN_DEFAULT: Final[int] = 5000
GSM_SCAN_INTERVAL_PRIMELAN_DEFAULT: Final[int] = 30000
SYSTEM_FAULTS_SCAN_INTERVAL_PRIMELAN_DEFAULT: Final[int] = 15000
PANEL_LOG_EVENTS_SCAN_INTERVAL_PRIMELAN_DEFAULT: Final[int] = 15000

SCAN_INTERVAL_MIN: Final[int] = 1
SCAN_INTERVAL_MAX: Final[int] = 300000

STORAGE_KEY_LAST_PANEL_EVENT_LOGS = "last_panel_event_logs"
STORAGE_KEY_OUTPUT_LABELS = "primelan_output_labels"

INIM_PRIME_DEVICE_MANUFACTURER = "Inim"
INIM_PRIME_MODEL_ZONE = "Prime Zone"

# Outputs / scenarios
OUTPUTS_SCAN_INTERVAL_PRIMELAN_DEFAULT: Final[int] = 30000
OUTPUTS_SCAN_INTERVAL_NATIVE_DEFAULT: Final[int] = 500
SCENARIOS_SCAN_INTERVAL_PRIMELAN_DEFAULT: Final[int] = 10000
# When BOTH backends are configured, the live data comes from Native, so the
# PrimeLAN-only pollers (GSM, system faults, event log) can run much slower by
# default to cut PrimeLAN traffic. Scenarios stay faster (they drive the alarm
# panel). The user can still override any of these in the options.
PRIMELAN_SCAN_INTERVAL_BOTH_DEFAULT: Final[int] = 60000
TIMERS_SCAN_INTERVAL_DEFAULT: Final[int] = 10000

# Roller shutters ("tapparelle"): Smart modules driven via their motor relays
# (terminal T04 = open, T03 = close). Full-travel times are the panel's own
# programmed values; HA estimates position from them. The extra margin is held
# at the end of a full open/close to recalibrate to 0 % / 100 %.
# Length of the trigger pulse sent to a shutter command output; the Smart
# module runs the full travel on its own from that pulse.
SHUTTER_PULSE_MS: Final[int] = 500

# Domotic-module description table (native memory): one 16-byte label per
# module, module 1 first. Used to auto-name the shutters ("TENDA SALA", ...).
# Found by decrypting a Prime/STUDIO "Moduli domotici" read.
MODULE_LABELS_ADDRESS: Final[int] = 335799424
MODULE_LABEL_SIZE: Final[int] = 16
MODULE_LABELS_COUNT: Final[int] = 30

# Thermostats (temperature sensors). The status command returns the live
# temperatures; the names come from two contiguous label tables (keypads then
# sensors). Addresses/command found by decrypting a Prime/STUDIO capture.
THERMOSTAT_STATUS_OPERATION: Final[int] = 0x2B
THERMOSTAT_STATUS_LENGTH: Final[int] = 122
KEYPAD_LABELS_ADDRESS: Final[int] = 335801888
KEYPAD_LABELS_COUNT: Final[int] = 30
SENSOR_LABELS_ADDRESS: Final[int] = 335802368
SENSOR_LABELS_COUNT: Final[int] = 15
THERMOSTATS_SCAN_INTERVAL_DEFAULT: Final[int] = 30000

# Native scenarios, system status and event log: the commands the Inim app
# sends in local (P2P) mode, read from a decrypted capture. Like the
# thermostat status, they carry the master PIN (74 00 00 00 00 00) instead of
# a user code. Each payload starts with the operation (4 bytes LE).
NATIVE_MASTER_PIN: Final[bytes] = b"\x74\x00\x00\x00\x00\x00"
# op 5: scenarios, + partition mask (4) + PIN + 0 = active / 1 = programmed;
# the answer holds the scenario bitmask from byte 20 on.
NATIVE_SCENARIOS_OPERATION: Final[int] = 5
NATIVE_SCENARIOS_PARTITION_MASK: Final[bytes] = b"\xff\xff\x0f\x00"
NATIVE_SCENARIOS_LENGTH: Final[int] = 27
NATIVE_SCENARIOS_MASK_OFFSET: Final[int] = 20
NATIVE_SCENARIOS_MASK_SIZE: Final[int] = 7
# op 60 then op 59 activate a scenario (0-based), as the app does.
NATIVE_SCENARIO_PREPARE_OPERATION: Final[int] = 60
NATIVE_SCENARIO_PREPARE_LENGTH: Final[int] = 270
NATIVE_SCENARIO_ACTIVATE_OPERATION: Final[int] = 59
NATIVE_SCENARIO_ACTIVATE_LENGTH: Final[int] = 271
# op 13: system status, fault bits (same bits as PrimeLAN) at byte 4 and the
# supply voltage in tenths of a volt at byte 20.
NATIVE_SYSTEM_STATUS_OPERATION: Final[int] = 13
NATIVE_SYSTEM_STATUS_LENGTH: Final[int] = 58
NATIVE_FAULTS_OFFSET: Final[int] = 4
NATIVE_SUPPLY_VOLTAGE_OFFSET: Final[int] = 20
# op 18: event log (next index, record size, address, capacity); op 31: the
# address and size of the event type table (5 bytes per type).
NATIVE_LOG_INFO_OPERATION: Final[int] = 18
NATIVE_LOG_INFO_LENGTH: Final[int] = 18
NATIVE_LOG_TABLES_OPERATION: Final[int] = 31
NATIVE_LOG_TABLES_LENGTH: Final[int] = 28
NATIVE_EVENT_TYPE_SIZE: Final[int] = 5
# Label pointer table (one 4-byte LE address per table).
NATIVE_LABEL_POINTERS_ADDRESS: Final[int] = 0x1A002400
NATIVE_LABEL_POINTERS: Final[dict[str, int]] = {
    "area": 20, "zone": 24, "user": 28, "reader": 32, "keypad": 36,
    "expansion": 40, "key": 44, "phone": 48, "scenario": 52, "timer": 56,
    "event_text": 64, "siren": 68,
}
# Object kind (4th byte of an event type) -> label table.
NATIVE_EVENT_OBJECT_KINDS: Final[dict[int, str]] = {
    0x01: "zone", 0x0C: "zone", 0x02: "area", 0x03: "user", 0x04: "key",
    0x05: "phone", 0x06: "timer", 0x08: "expansion", 0x09: "keypad",
    0x0A: "reader", 0x0B: "siren", 0x0D: "scenario",
}
# Users not in the code table (e.g. 255) are commands without a user code:
# Home Assistant itself.
NATIVE_NO_USER_LABEL: Final[str] = "Home Assistant"
NATIVE_REMOTE_LABEL: Final[str] = "Da remoto"
SCENARIOS_SCAN_INTERVAL_NATIVE_DEFAULT: Final[int] = 2000
SYSTEM_FAULTS_SCAN_INTERVAL_NATIVE_DEFAULT: Final[int] = 10000
PANEL_LOG_EVENTS_SCAN_INTERVAL_NATIVE_DEFAULT: Final[int] = 10000

# Per-shutter option field labels (shown as the field names in the options form,
# and stored as the keys of the shutter command mapping).
SHUTTER_FIELD_OPEN = "Aperto"
SHUTTER_FIELD_CLOSE = "Chiuso"
# Partial-open preset field label -> position percent.
SHUTTER_PRESET_FIELDS = {"1/4": 25, "1/2": 50, "3/4": 75}
SHUTTER_COMMAND_FIELDS = (SHUTTER_FIELD_OPEN, SHUTTER_FIELD_CLOSE, *SHUTTER_PRESET_FIELDS)
