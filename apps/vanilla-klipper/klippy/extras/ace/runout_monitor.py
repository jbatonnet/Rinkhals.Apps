"""
Runout monitoring module for ACE Pro filament management system.

This module handles filament runout detection during printing, coordinating
with the endless spool system for automatic material swapping when runout
is detected.
"""

from .config import (
    SENSOR_TOOLHEAD,
    get_instance_from_tool,
    get_local_slot,
    ACE_INSTANCES,
)


class RunoutMonitor:
    """
    Monitors filament sensors during printing and handles runout detection.

    Responsibilities:
    - Track sensor state changes during print
    - Detect filament runout (sensor present → absent transition)
    - Coordinate with endless spool for automatic material swapping
    - Show user prompts when manual intervention needed
    - Manage runout handling state machine

    The monitor runs as a periodic callback registered with the Klipper reactor,
    checking sensor states and print status to detect runout events.
    """

    def __init__(self, printer, gcode, reactor, endless_spool, manager):
        """
        Initialize runout monitor.

        Args:
            printer: Klipper printer object for accessing printer state
            gcode: Klipper gcode object for sending commands and responses
            reactor: Klipper reactor for timer management
            endless_spool: EndlessSpool instance for automatic swapping
            manager: AceManager instance (for sensor queries and state)
        """
        self.printer = printer
        self.gcode = gcode
        self.reactor = reactor
        self.endless_spool = endless_spool
        self.manager = manager  # Reference back to manager for sensor queries

        # State tracking
        self.prev_toolhead_sensor_state = None
        self.last_printing_active = False
        self.last_print_state = "idle"
        self.monitor_debug_counter = 0

        # Control flags
        self.runout_detection_active = False
        self.runout_handling_in_progress = False

        # Timer handle
        self._monitoring_timer = None

    def start_monitoring(self):
        """Start runout detection monitor loop."""
        self.gcode.respond_info("ACE: Starting runout detection monitor")
        self.set_detection_active(True)
        self._monitoring_timer = self.reactor.register_timer(
            self._monitor_runout,
            self.reactor.NOW
        )

    def stop_monitoring(self):
        """Stop runout monitoring."""
        self.gcode.respond_info("ACE: Stopping runout detection monitor")
        self.set_detection_active(False)
        if self._monitoring_timer:
            try:
                self.reactor.unregister_timer(self._monitoring_timer)
            except Exception:
                pass
            self._monitoring_timer = None

    def set_detection_active(self, active):
        """
        Enable/disable runout detection with tracing.

        Args:
            active: True to enable detection, False to disable

        Returns:
            bool: The new active state
        """
        old_state = self.runout_detection_active
        self.runout_detection_active = active

        if old_state != active:
            state_str = 'ENABLED' if active else 'DISABLED'
            self.gcode.respond_info(
                f"ACE: Runout detection {state_str} "
                f"(was: {old_state}, now: {active}, "
                f"toolchange_in_progress={self.manager.toolchange_in_progress})"
            )

        return active

    def _monitor_runout(self, eventtime):
        """
        Monitor filament runout during printing.

        This is the main monitoring loop that runs periodically via reactor timer.
        It tracks print state, sensor states, and detects runout events.

        Args:
            eventtime: Current event time from reactor

        Returns:
            float: Next callback time (eventtime + interval)
        """
        # Get current state
        print_stats = self.printer.lookup_object("print_stats", None)
        is_printing = False
        raw_print_state = ""
        if print_stats:
            try:
                stats = print_stats.get_status(eventtime)
                raw_print_state = (stats.get("state") or "").lower()
                is_printing = raw_print_state == "printing"
            except Exception:
                is_printing = False
                raw_print_state = ""

        save_vars = self.printer.lookup_object("save_variables")
        variables = save_vars.allVariables
        current_tool = variables.get("ace_current_index", -1)
        current_sensor_state = self.manager.get_switch_state(SENSOR_TOOLHEAD)

        # Track state changes for logging
        old_printing_active = self.last_printing_active
        old_print_state = self.last_print_state
        self.last_printing_active = is_printing
        self.last_print_state = raw_print_state

        if old_print_state != raw_print_state:
            self.gcode.respond_info(f"ACE: Print state changed: {old_print_state} → {raw_print_state}")

        # Detect print start and force initialize
        print_just_started = (
            is_printing and
            not old_printing_active and
            raw_print_state == "printing" and
            current_tool >= 0
        )

        if print_just_started:
            self.gcode.respond_info("ACE: Print started - initializing runout detection")

            # Force initialize baseline
            self.prev_toolhead_sensor_state = current_sensor_state

            # Enable detection immediately if sensor shows filament
            if current_sensor_state:
                self.set_detection_active(True)
                self.gcode.respond_info(
                    f"ACE: Runout detection ENABLED at print start "
                    f"(sensor: True, tool: T{current_tool})"
                )
            else:
                self.gcode.respond_info(
                    f"ACE: Runout detection WAITING at print start "
                    f"(sensor: False, tool: T{current_tool})"
                )

            # Sync macro state
            try:
                self.gcode.run_script_from_command(
                    f"SET_GCODE_VARIABLE MACRO=_ACE_STATE VARIABLE=active VALUE={current_tool}"
                )
            except Exception as e:
                self.gcode.respond_info(f"ACE: Could not sync macro state: {e}")

            return eventtime + 0.05

        # DEBUG LOGGING every ~15 minutes
        self.monitor_debug_counter += 1
        if self.monitor_debug_counter >= 1200 * 15:
            self.monitor_debug_counter = 0
            self.gcode.respond_info(
                f"ACE: Monitor - Tool: T{current_tool}, "
                f"Printing: {is_printing} ({raw_print_state}), "
                f"Prev sensor: {self.prev_toolhead_sensor_state}, "
                f"Current sensor: {current_sensor_state}, "
                f"Detection active: {self.runout_detection_active}, "
                f"Toolchange: {self.manager.toolchange_in_progress}, "
                f"Runout handling: {self.runout_handling_in_progress}"
            )

            # For debugging: Auto-recovery check
            # WARN if detection should be active but isn't
            if (is_printing and
                    current_sensor_state and
                    not self.runout_detection_active and
                    current_tool >= 0 and
                    not self.manager.toolchange_in_progress and
                    not self.runout_handling_in_progress):

                self.gcode.respond_info(
                    "ACE: Autorecovery: ⚠ WARNING - Runout detection should be active but is disabled! "
                    "Attempting to enable..."
                )

                # Try to recover
                self.prev_toolhead_sensor_state = current_sensor_state
                self.set_detection_active(True)

                self.gcode.respond_info(
                    f"ACE: Autorecovery: Auto-recovery attempted - detection re-enabled "
                    f"(sensor: {current_sensor_state}, tool: T{current_tool})"
                )

        # Early exit if detection disabled or toolchange in progress
        if not self.runout_detection_active or self.manager.toolchange_in_progress:
            return eventtime + 0.2

        try:
            if current_tool < 0:
                # No active tool - nothing to monitor
                self.prev_toolhead_sensor_state = None
                return eventtime + 0.1

            print_just_stopped = old_printing_active and (not is_printing) and (raw_print_state != "paused")

            # PRINT STOPPED - clean up state
            if print_just_stopped:
                self.gcode.respond_info("ACE: Print stopped/cancelled - resetting monitor baseline")
                self.prev_toolhead_sensor_state = None
                self.runout_handling_in_progress = False

                if not self.runout_detection_active:
                    self.gcode.respond_info("ACE: Restoring runout monitoring after print stop")
                    self.set_detection_active(True)

                try:
                    self.gcode.run_script_from_command(
                        "SET_GCODE_VARIABLE MACRO=_ACE_STATE VARIABLE=active VALUE=-1"
                    )
                except Exception as e:
                    self.gcode.respond_info(f"ACE: Could not sync macro state on print stop: {e}")

                return eventtime + 0.2

            # PAUSED or NOT PRINTING - sleep/relax monitoring
            if raw_print_state == "paused" or not is_printing:
                self.prev_toolhead_sensor_state = None
                return eventtime + 0.2

            # Enhanced baseline initialization
            if self.prev_toolhead_sensor_state is None:
                self.prev_toolhead_sensor_state = current_sensor_state
                filament_pos = variables.get("ace_filament_pos", "bowden")

                self.gcode.respond_info(
                    f"ACE: Monitoring baseline established. "
                    f"Sensor: {'present' if current_sensor_state else 'absent'}, "
                    f"Tool: T{current_tool}, State: {filament_pos}"
                )

                # If sensor has filament and we're printing, enable detection immediately
                if current_sensor_state and is_printing and current_tool >= 0:
                    if not self.runout_detection_active:
                        self.set_detection_active(True)
                        self.gcode.respond_info("ACE: Runout detection enabled (baseline init)")

                # Sync macro state
                try:
                    self.gcode.run_script_from_command(
                        f"SET_GCODE_VARIABLE MACRO=_ACE_STATE VARIABLE=active VALUE={current_tool}"
                    )
                except Exception as e:
                    self.gcode.respond_info(f"ACE: Could not sync macro state: {e}")

                return eventtime + 0.05

            # ===== RUNOUT DETECTION - detect present → absent transition =====
            if self.prev_toolhead_sensor_state is True and current_sensor_state is False:
                # Runout detected!
                if self.runout_handling_in_progress:
                    self.gcode.respond_info("ACE: Runout detection suppressed (already handling runout)")
                    return eventtime + 0.2

                self.gcode.respond_info(
                    f"ACE: Runout detected on T{current_tool} (sensor: present → absent)"
                )

                self._handle_runout_detected(current_tool)

                self.prev_toolhead_sensor_state = current_sensor_state
                return eventtime + 0.2

            # Update previous state for next cycle
            self.prev_toolhead_sensor_state = current_sensor_state
            return eventtime + 0.05

        except self.printer.command_error as e:
            # Klipper printer error
            error_msg = str(e)
            if "shutdown" in error_msg.lower() or "lost communication" in error_msg.lower():
                self.gcode.respond_info("ACE: Monitor stopped due to printer shutdown/MCU disconnect")
                self.set_detection_active(False)
                self.runout_handling_in_progress = False
                return self.reactor.NEVER
            else:
                self.gcode.respond_info(f"ACE: Monitor command error: {e}")
                return eventtime + 1.0

        except Exception as e:
            self.gcode.respond_info(f"ACE: Monitor error: {e}")
            return eventtime + 1.0

    def _show_runout_prompt(self, tool_index, instance_num, local_slot, material, color):
        """
        Show simple Mainsail prompt for runout with CANCEL/RESUME buttons.

        Args:
            tool_index: Global tool index (e.g., 0-7)
            instance_num: ACE instance number
            local_slot: Local slot number on instance
            material: Material type (e.g., "PLA")
            color: RGB color array [r, g, b]
        """
        self.gcode.run_script_from_command(
            'RESPOND TYPE=command MSG="action:prompt_begin Filament Runout"'
        )

        color_str = f"RGB({color[0]},{color[1]},{color[2]})"
        prompt_text = (
            f"Filament runout detected on Tool T{tool_index}! "
            f"Please refill ACE {instance_num} Slot {local_slot} with {material} filament "
            f"(Color: {color_str})."
        )

        self.gcode.run_script_from_command(
            f'RESPOND TYPE=command MSG="action:prompt_text {prompt_text}"'
        )

        self.gcode.run_script_from_command(
            f'RESPOND TYPE=command MSG="action:prompt_button Retry T{tool_index}|T{tool_index}|primary"'
        )

        self.gcode.run_script_from_command(
            'RESPOND TYPE=command MSG="action:prompt_button Extrude 100mm|'
            '_EXTRUDE LENGTH=100 SPEED=300|secondary"'
        )

        self.gcode.run_script_from_command(
            'RESPOND TYPE=command MSG="action:prompt_button Retract 100mm|'
            '_RETRACT LENGTH=100 SPEED=300|secondary"'
        )

        self.gcode.run_script_from_command(
            'RESPOND TYPE=command MSG="action:prompt_footer_button Resume|RESUME|primary"'
        )

        self.gcode.run_script_from_command(
            'RESPOND TYPE=command MSG="action:prompt_footer_button Cancel Print|CANCEL_PRINT|error"'
        )

        self.gcode.run_script_from_command(
            'RESPOND TYPE=command MSG="action:prompt_show"'
        )

    def _handle_runout_detected(self, tool_index):
        """
        Handle filament runout detection.

        Flow:
        1. Pause the print immediately
        2. Show interactive prompt with CANCEL/RESUME options
        3. Check if endless spool is enabled
        4. If enabled: try to find exact material/color match in other slots
        5. If match found: close prompt, perform automatic tool swap and resume
        6. If no match or endless spool disabled: stay paused (user must refill)

        Resets sensor tracking to prevent repeated triggers.

        Args:
            tool_index: Tool index where runout was detected
        """
        self.gcode.respond_info(f"ACE: Runout detected on T{tool_index}")
        self.runout_handling_in_progress = True
        self.prev_toolhead_sensor_state = None

        try:
            # Step 1: PAUSE immediately
            self._pause_for_runout()

            # Get runout details for prompt
            instance_num = get_instance_from_tool(tool_index)
            material = "unknown"
            color = [0, 0, 0]
            local_slot = -1

            if instance_num >= 0:
                local_slot = get_local_slot(tool_index, instance_num)
                ace_inst = ACE_INSTANCES.get(instance_num)
                if ace_inst and 0 <= local_slot < len(ace_inst.inventory):
                    inv = ace_inst.inventory[local_slot]
                    material = inv.get("material", "unknown")
                    color = inv.get("color", [0, 0, 0])
                    self.gcode.respond_info(
                        f"ACE: Runout on T{tool_index}: {material} "
                        f"RGB({color[0]},{color[1]},{color[2]})"
                    )

            # Step 3: Show simple interactive prompt
            self._show_runout_prompt(tool_index, instance_num, local_slot, material, color)

            # Step 4: Check if endless spool is enabled
            save_vars = self.printer.lookup_object("save_variables", None)
            endless_spool_enabled = False
            if save_vars:
                endless_spool_enabled = save_vars.allVariables.get("ace_endless_spool_enabled", False)

            if not endless_spool_enabled:
                self.gcode.respond_info(
                    "ACE: Endless spool disabled. Staying paused. "
                    "Refill spool and resume manually."
                )
                return

            # Step 5: Try to find exact material/color match
            next_tool = self.endless_spool.find_exact_match(tool_index)
            if next_tool < 0:
                self.gcode.respond_info(
                    f"ACE: No endless spool match found for T{tool_index}. "
                    f"Staying paused. Refill spool or load matching material."
                )
                return

            # Step 6: Match found - close prompt and execute automatic swap
            self.gcode.respond_info(
                f"ACE: Endless spool match found: T{tool_index} → T{next_tool}"
            )

            # Close prompt before auto-swap (since we're handling it automatically)
            self.gcode.run_script_from_command(
                'RESPOND TYPE=command MSG="action:prompt_end"'
            )

            self.endless_spool.execute_swap(tool_index, next_tool)

        except Exception as e:
            self.gcode.respond_info(f"ACE: Runout handling error: {e}")
        finally:
            self.runout_handling_in_progress = False

    def _pause_for_runout(self):
        """
        Pause the print for runout handling.

        Uses Klipper's PAUSE command to stop the print and move
        toolhead to safe position.
        """
        try:
            self.gcode.respond_info("ACE: Pausing print")
            self.gcode.run_script_from_command("PAUSE")
        except Exception as e:
            self.gcode.respond_info(f"ACE: Error pausing print: {e}")
