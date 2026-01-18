"""
ACE Pro Klipper Module - Multiple filament unit support

This module implements support for one or more ACE Pro filament AMS units
integrated with Klipper/Klippy.

Architecture:
- AceSerialManager: Handles serial communication (one per unit)
- AceInstance: Manages a single physical ACE unit (4 slots)
- AceManager: Orchestrates multiple instances, runs global monitoring
- Commands: G-code command implementations

Directory structure:
  ace/__init__.py          ← Entry point for Klipper
  ace/config.py            ← Constants, helpers, global registry
  ace/serial_manager.py    ← Serial communication
  ace/instance.py          ← Per-unit logic
  ace/manager.py           ← Multi-instance orchestration
  ace/commands.py          ← G-code commands

Configuration:
    [ace]
    ace_count: 1            # Number of ACE instances (default: 1)
                            # 1 = instance 0 (T0-T3)
                            # 2 = instances 0,1 (T0-T3, T4-T7)
                            # etc.
    baud: 115200
    feed_speed: 60
    # ... shared settings for all instances

To use:
    PLACE THIS ace/ DIRECTORY IN klipper/klippy/extras/
    Add single [ace] section to printer.cfg with ace_count parameter
"""

import logging

from .manager import AceManager
from .commands import register_all_commands


def load_config(config):
    """
    Load ACE module for [ace] section.

    """

    # Create ONE manager for ALL instances
    ace_manager = AceManager(config)

    # Register all commands (done once in manager)
    printer = config.get_printer()
    register_all_commands(printer)
    logging.info("ACE: Registered all gcode commands")

    # Register each ACE instance with the printer so KlipperScreen can access them
    # Instance 0 is registered as "ace", others as "ace 1", "ace 2", etc.
    for instance_num, instance in enumerate(ace_manager.instances):
        instance_name = f"ace_instance_{instance_num}"
        printer.add_object(instance_name, instance)
        logging.info(
            f"ACE: Registered printer object '{instance_name}' "
            f"(has_get_status={hasattr(instance, 'get_status')})"
        )

    # Return the AceManager for Klipper
    return ace_manager
