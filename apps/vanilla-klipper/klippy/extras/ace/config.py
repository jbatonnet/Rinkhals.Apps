"""
Configuration and constants for ACE Pro module.

Holds shared constants, configuration loading, and utility functions
that don't depend on specific instances.
"""

import re


# ========== ACE Instance Constants ==========

# Filament position state constants
FILAMENT_STATE_BOWDEN = "bowden"        # In bowden before splitter (unloaded, path free)
FILAMENT_STATE_SPLITTER = "splitter"    # Possibly in splitter (between RDM and toolhead (loaded))
FILAMENT_STATE_TOOLHEAD = "toolhead"    # At toolhead sensor
FILAMENT_STATE_NOZZLE = "nozzle"        # In hotend/nozzle

# Sensor name constants
SENSOR_TOOLHEAD = 'toolhead_sensor'
SENSOR_RDM = 'return_module'

# Slots per ACE unit (fixed)
SLOTS_PER_ACE = 4

# Retry configuration for unload/load operations
UNLOAD_RETRY_ATTEMPTS = 3              # Number of retry attempts
UNLOAD_RETRY_DELAY = 0.5               # Seconds between attempts
UNLOAD_INITIAL_LENGTH = 50             # mm for first attempt
UNLOAD_SPEED_MULTIPLIERS = [1.0, 0.7, 0.4]  # Speed factors per attempt

# Max retries for ACE command operations (feed/retract)
MAX_RETRIES = 6
# RFID state constants (from ACE hardware status responses)
RFID_STATE_NO_INFO = 0         # Information not found (no RFID tag)
RFID_STATE_FAILED = 1          # Failed to identify tag
RFID_STATE_IDENTIFIED = 2      # Successfully identified tag
RFID_STATE_IDENTIFYING = 3     # Currently identifying tag

# RFID inventory sync configuration
# When enabled, ACE hardware status updates (from RFID or manual changes)
# automatically sync material/color data to Klipper inventory
RFID_INVENTORY_SYNC_ENABLED = True  # Default: enabled

# Global registry for instances (populated at load time)
ACE_INSTANCES = {}
INSTANCE_MANAGERS = {}  # Maps instance_num -> AceManager

# Purge settings (can be overridden globally via gcode command)
GLOBAL_PURGE_LENGTH = None
GLOBAL_PURGE_SPEED = None


# ========== Configuration Helpers ==========
def read_ace_config(config):
    """
    Read and validate all ACE config values, return as dict.
    Config values support per-instance overrides.
    """
    ace_config = {}

    # Non-overridable settings (apply to all instances)
    ace_config["ace_count"] = config.getint("ace_count", 1)
    ace_config["baud"] = config.getint("baud", 115200)
    ace_config["filament_runout_sensor_name_rdm"] = config.get(
        "filament_runout_sensor_name_rdm", None
    )
    ace_config["filament_runout_sensor_name_nozzle"] = config.get(
        "filament_runout_sensor_name_nozzle", "filament_runout_nozzle"
    )
    ace_config["feed_assist_active_after_ace_connect"] = config.getboolean(
        "feed_assist_active_after_ace_connect", True
    )
    ace_config["rfid_inventory_sync_enabled"] = config.getboolean(
        "rfid_inventory_sync_enabled", True
    )
    # RFID temperature mode: how to calculate print temp from min/max
    # Options: "average" (default), "min", "max"
    ace_config["rfid_temp_mode"] = config.get("rfid_temp_mode", "average").lower()
    if ace_config["rfid_temp_mode"] not in ("average", "min", "max"):
        ace_config["rfid_temp_mode"] = "average"

    ace_config["parkposition_to_toolhead_length"] = config.getint("parkposition_to_toolhead_length", 1000)
    ace_config["parkposition_to_rdm_length"] = config.getint("parkposition_to_rdm_length", 150)
    ace_config["toolhead_retraction_speed"] = config.getint("toolhead_retraction_speed", 10)
    ace_config["toolhead_retraction_length"] = config.getint("toolhead_retraction_length", 40)
    ace_config["toolhead_full_purge_length"] = config.getint("toolhead_full_purge_length", 22)
    ace_config["toolhead_slow_loading_speed"] = config.getint("toolhead_slow_loading_speed", 5)
    ace_config["extruder_feeding_length"] = config.getint("extruder_feeding_length", 1)
    ace_config["extruder_feeding_speed"] = config.getint("extruder_feeding_speed", 5)
    ace_config["timeout_multiplier"] = config.getint("timeout_multiplier", 2)
    ace_config["default_color_change_purge_length"] = config.getint("default_color_change_purge_length", "50")
    ace_config["default_color_change_purge_speed"] = config.getint("default_color_change_purge_speed", "400")
    ace_config["purge_max_chunk_length"] = config.getint("purge_max_chunk_length", "300")
    ace_config["purge_multiplier"] = config.getfloat("purge_multiplier", "1.0")
    ace_config["pre_cut_retract_length"] = config.getint("pre_cut_retract_length", "2")
    ace_config["status_debug_logging"] = config.getboolean("status_debug_logging", False)
    ace_config["ace_connection_supervision"] = config.getboolean(
        "ace_connection_supervision", True
    )
    # STORE RAW CONFIG STRINGS (will be parsed per-instance)
    # These support instance-specific overrides via "value" or "value,inst:override"
    ace_config["feed_speed"] = config.get("feed_speed", "60")
    ace_config["retract_speed"] = config.get("retract_speed", "50")
    ace_config["total_max_feeding_length"] = config.get("total_max_feeding_length", "2500")
    ace_config["toolchange_load_length"] = config.get("toolchange_load_length", "3000")
    ace_config["incremental_feeding_length"] = config.get("incremental_feeding_length", "50")
    ace_config["incremental_feeding_speed"] = config.get("incremental_feeding_speed", "30")
    ace_config["heartbeat_interval"] = config.get("heartbeat_interval", "1.0")
    ace_config["max_dryer_temperature"] = config.get("max_dryer_temperature", "60")

    return ace_config


def get_tool_offset(instance_num):
    """Get the first tool index managed by this instance."""
    return instance_num * SLOTS_PER_ACE


def get_ace_instance_and_slot_for_tool(tool):
    """
    Find ACE instance and local slot for a given tool index.

    Args:
        tool: Global tool index (0+)

    Returns:
        tuple: (ace_instance, local_slot) or (None, -1) if not found
    """
    instance_num = get_instance_from_tool(tool)

    if instance_num == -1:
        return None, -1

    local_slot = get_local_slot(tool, instance_num)

    if local_slot == -1:
        return None, -1

    current_ace = ACE_INSTANCES.get(instance_num)

    return current_ace, local_slot


def get_instance_from_tool(tool_index):
    """
    Find which ACE instance manages a given tool index.

    Args:
        tool_index: Global tool index (0+)

    Returns:
        int: Instance number, or -1 if not managed by any instance
    """
    if tool_index < 0:
        return -1

    instance_num = tool_index // SLOTS_PER_ACE

    # Verify instance exists
    if instance_num in ACE_INSTANCES:
        return instance_num

    return -1


def get_local_slot(tool_index, instance_num):
    """
    Get local slot (0-3) for a tool index on a given instance.

    Args:
        tool_index: Global tool index
        instance_num: ACE instance number

    Returns:
        int: Local slot (0-3), or -1 if tool not managed by instance
    """
    instance_offset = instance_num * SLOTS_PER_ACE
    local_slot = tool_index - instance_offset

    if 0 <= local_slot < SLOTS_PER_ACE:
        return local_slot

    return -1


def parse_instance_number(name):
    """
    Parse ACE instance number from config section name.

    Examples:
        "ace" → 0
        "ace 0" → 0
        "ace1" → 1
        "ace 3" → 3

    Args:
        name: Config section name

    Returns:
        int: Instance number
    """
    if not name:
        return 0

    name = name.strip().lower()

    if name == "ace":
        return 0

    m = re.match(r'^ace(?:[\s_]+)?(\d+)?$', name)
    if m:
        suffix = m.group(1)
        if suffix is not None:
            return int(suffix)

    return 0


def create_empty_inventory_slot():
    """Create empty inventory slot dict."""
    return {
        "status": "empty",
        "color": [0, 0, 0],
        "material": "",
        "temp": 0,
        "rfid": False,
    }


def create_inventory(slot_count=SLOTS_PER_ACE):
    """Create empty inventory for all slots."""
    return [create_empty_inventory_slot() for _ in range(slot_count)]


def create_status_dict(slot_count=SLOTS_PER_ACE):
    """Create empty status dict."""
    return {
        'status': 'ready',
        'dryer': {
            'status': 'stop',
            'target_temp': 0,
            'duration': 0,
            'remain_time': 0
        },
        'temp': 0,
        'enable_rfid': 1,
        'fan_speed': 7000,
        'feed_assist_count': 0,
        'cont_assist_time': 0.0,
        'slots': [
            {
                'index': i,
                'status': 'empty',
                'sku': '',
                'type': '',
                'color': [0, 0, 0]
            } for i in range(slot_count)
        ]
    }


def set_and_save_variable(printer, gcode, varname, value):
    """
    Set and persist a save_variable to Klipper's save_variables.

    Converts Python types to Klipper-compatible format:
    - Booleans: True/False (uppercase, Python literals)
    - Strings: Wrapped in single quotes + double quotes for proper escaping
    - Dicts/Lists: JSON serialized
    - Numbers: String representation

    Args:
        printer: Klipper printer object
        gcode: Klipper gcode object
        varname: Variable name (string)
        value: Value to save (any JSON-serializable type)
    """
    import json

    save_vars = printer.lookup_object("save_variables")
    variables = save_vars.allVariables

    variables[varname] = value

    if isinstance(value, bool):
        formatted_value = "True" if value else "False"
        gcode.run_script_from_command(
            f"SAVE_VARIABLE VARIABLE={varname} VALUE={formatted_value}"
        )
    elif isinstance(value, str):
        # Match working manager.py version: wrap in single quotes + double quotes
        gcode.run_script_from_command(
            f"SAVE_VARIABLE VARIABLE={varname} VALUE='\"{value}\"'"
        )
    elif isinstance(value, (dict, list)):
        # Dicts/Lists: JSON serialized
        gcode.run_script_from_command(
            f"SAVE_VARIABLE VARIABLE={varname} VALUE={json.dumps(value)}"
        )
    else:
        # Numbers and other types
        gcode.run_script_from_command(
            f"SAVE_VARIABLE VARIABLE={varname} VALUE={value}"
        )


def parse_instance_config(config_value, instance_num, param_name):
    """
    Parse config value that may contain per-instance overrides.

    Formats supported:
      - Simple: "1000" → use 1000 for all instances
      - Global + override: "1000,2:500" → 1000 for all except instance 2 (uses 500)
      - Explicit all: "0:1000,1:400,2:2000" → per-instance values

    Args:
        config_value: String from config file
        instance_num: Instance number (0, 1, 2, ...)
        param_name: Parameter name (for error messages)

    Returns:
        int/float: Resolved value for this instance

    Examples:
        >>> parse_instance_config("1000", 0, "length")
        1000
        >>> parse_instance_config("1000,2:500", 2, "length")
        500
        >>> parse_instance_config("1000,2:500", 0, "length")
        1000
        >>> parse_instance_config("0:1000,1:400,2:2000", 1, "length")
        400
    """
    value_str = str(config_value).strip()

    # Check if it contains instance overrides (has colons)
    if ':' not in value_str:
        # Simple value - use for all instances
        try:
            return int(value_str) if '.' not in value_str else float(value_str)
        except ValueError:
            raise ValueError(
                f"Invalid config value for {param_name}: '{value_str}'"
            )

    # Parse instance-specific overrides
    parts = value_str.split(',')
    instance_map = {}
    global_default = None

    for part in parts:
        part = part.strip()
        if ':' in part:
            # Instance-specific: "2:500"
            inst_str, val_str = part.split(':', 1)
            try:
                inst = int(inst_str.strip())
                val = int(val_str.strip()) if '.' not in val_str else float(val_str.strip())
                instance_map[inst] = val
            except ValueError:
                raise ValueError(
                    f"Invalid instance override for {param_name}: '{part}'"
                )
        else:
            # Global default (must be first part)
            if global_default is not None:
                raise ValueError(
                    f"Multiple global defaults for {param_name}: '{value_str}'"
                )
            try:
                global_default = int(part) if '.' not in part else float(part)
            except ValueError:
                raise ValueError(
                    f"Invalid global default for {param_name}: '{part}'"
                )

    # Return value for this instance
    if instance_num in instance_map:
        return instance_map[instance_num]
    elif global_default is not None:
        return global_default
    else:
        raise ValueError(
            f"No value found for instance {instance_num} in {param_name}: '{value_str}'"
        )


OVERRIDABLE_PARAMS = [
    "feed_speed",
    "retract_speed",
    "total_max_feeding_length",
    "toolchange_load_length",
    "incremental_feeding_length",
    "incremental_feeding_speed",
    "heartbeat_interval",
    "max_dryer_temperature"
]
