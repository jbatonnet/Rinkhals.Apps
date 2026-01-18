"""
EndlessSpool: Handles endless spool filament matching logic.

Responsibilities:
- Find exact material/color matches across all slots
- No pause/resume logic (manager owns that)
- No jam detection (removed)

Integration:
- Called from: AceManager._handle_runout_detected()
"""

import logging
from .config import (
    ACE_INSTANCES,
    SLOTS_PER_ACE,
    get_instance_from_tool,
    get_local_slot,
)


class EndlessSpool:
    """Endless spool matching logic only."""

    def __init__(self, printer, gcode, manager):
        """
        Initialize endless spool handler.

        Args:
            printer: Klipper printer object
            gcode: Klipper gcode object
            manager: AceManager instance
        """
        self.printer = printer
        self.gcode = gcode
        self.manager = manager
        self.reactor = printer.get_reactor()

    def get_match_mode(self):
        """
        Get the current endless spool match mode from persistent storage.

        Returns:
            str: "exact" (material + color), "material" (material only),
                 or "next" (first ready spool regardless of material/color)
        """
        save_vars = self.printer.lookup_object("save_variables", None)
        mode = "exact"

        if save_vars:
            mode = save_vars.allVariables.get("ace_endless_spool_match_mode", "exact")

        # Normalize/guardrail unexpected values
        if mode not in {"exact", "material", "next"}:
            mode = "exact"

        return mode

    def find_exact_match(self, current_tool):
        """
        Find next spool with matching material and optionally color.

        Match mode (configurable via ace_endless_spool_match_mode):
        - "exact": Match both material AND color (default)
        - "material": Match material only, ignore color

        Searches all slots in all instances, starting from the next tool
        and wrapping around.

        Args:
            current_tool: Tool index with runout (0-based)

        Returns:
            int: Tool index of match, or -1 if none found
        """
        inst_num = get_instance_from_tool(current_tool)
        local_slot = get_local_slot(current_tool, inst_num)

        if inst_num < 0 or local_slot < 0:
            return -1

        ace_inst = ACE_INSTANCES.get(inst_num)
        if not ace_inst:
            return -1

        current_inv = ace_inst.inventory[local_slot]
        current_material = current_inv.get("material", "").lower().strip()
        current_color = current_inv.get("color", [0, 0, 0])

        match_mode = self.get_match_mode()

        if match_mode == "material":
            logging.info(
                f"ACE: Looking for endless spool match (MATERIAL ONLY): {current_material}"
            )
        elif match_mode == "next":
            logging.info("ACE: Looking for endless spool match (NEXT READY SPOOL)")
        else:
            logging.info(
                f"ACE: Looking for endless spool match (EXACT): "
                f"{current_material} RGB({current_color[0]},{current_color[1]},{current_color[2]})"
            )

        total_tools = len(ACE_INSTANCES) * SLOTS_PER_ACE

        for offset in range(1, total_tools):
            candidate_tool = (current_tool + offset) % total_tools

            candidate_inst_num = get_instance_from_tool(candidate_tool)
            candidate_local_slot = get_local_slot(candidate_tool, candidate_inst_num)

            if candidate_inst_num < 0 or candidate_local_slot < 0:
                continue

            cand_ace = ACE_INSTANCES.get(candidate_inst_num)
            if not cand_ace:
                continue

            cand_inv = cand_ace.inventory[candidate_local_slot]

            cand_status = cand_inv.get("status")
            if cand_status != "ready":
                logging.info(
                    f"ACE: T{candidate_tool} skipped - status={cand_status} (need 'ready')"
                )
                continue

            # In "next" mode we ignore material/color and take the first ready slot
            if match_mode == "next":
                logging.info(f"ACE: Match found (next ready): T{candidate_tool}")
                return candidate_tool

            cand_material = cand_inv.get("material", "").lower().strip()

            # SAFETY: Never match "unknown" materials - we don't know if they're compatible!
            if current_material == "unknown" or cand_material == "unknown":
                logging.info(
                    f"ACE: T{candidate_tool} skipped - cannot match unknown materials "
                    f"(current='{current_material}', candidate='{cand_material}')"
                )
                continue

            if cand_material != current_material:
                logging.info(
                    f"ACE: T{candidate_tool} skipped - material mismatch "
                    f"(want '{current_material}', got '{cand_material}')"
                )
                continue

            if match_mode == "exact":
                cand_color = cand_inv.get("color", [0, 0, 0])
                if cand_color != current_color:
                    logging.info(
                        f"ACE: T{candidate_tool} skipped - color mismatch "
                        f"(want RGB({current_color[0]},{current_color[1]},{current_color[2]}), "
                        f"got RGB({cand_color[0]},{cand_color[1]},{cand_color[2]}))"
                    )
                    continue

            logging.info(
                f"ACE: Match found: T{current_tool} → T{candidate_tool}"
            )
            return candidate_tool

        if match_mode == "material":
            logging.info(
                f"ACE: No match for T{current_tool} (material: {current_material})"
            )
        elif match_mode == "next":
            logging.info(
                f"ACE: No ready spool available for T{current_tool}"
            )
        else:
            logging.info(
                f"ACE: No match for T{current_tool} "
                f"({current_material}, RGB({current_color[0]},{current_color[1]},{current_color[2]}))"
            )
        return -1

    def execute_swap(self, from_tool, to_tool):
        """
        Execute endless spool swap with intelligent fallback.

        On feed failure:
        1. Smart unload the failed tool (to_tool)
        2. Search for next matching spool
        3. Retry with new candidate
        """
        self.gcode.respond_info(f"ACE: Endless spool swap: T{from_tool} → T{to_tool}")

        tried_tools = {from_tool}
        current_target_tool = to_tool
        max_swap_attempts = 3

        try:
            for swap_attempt in range(max_swap_attempts):
                try:
                    self.gcode.respond_info(
                        f"ACE: Tool change attempt {swap_attempt + 1}/{max_swap_attempts}: "
                        f"T{from_tool} → T{current_target_tool}"
                    )

                    from_inst_num = get_instance_from_tool(from_tool)
                    from_slot = get_local_slot(from_tool, from_inst_num)
                    if from_inst_num >= 0 and from_slot >= 0:
                        ace_inst = self.manager.instances[from_inst_num]
                        if ace_inst:
                            ace_inst.inventory[from_slot]["status"] = "empty"
                            self.manager._sync_inventory_to_persistent(from_inst_num)
                            self.gcode.respond_info(f"ACE: Marked T{from_tool} as empty")

                    status = self.manager.perform_tool_change(from_tool, current_target_tool, is_endless_spool=True)
                    self.gcode.respond_info(f"ACE: {status}")

                    self.gcode.respond_info("ACE: Resuming print")
                    self.manager.gcode.run_script_from_command("RESUME PURGE=0")

                    return

                except Exception as load_error:
                    self.gcode.respond_info(
                        f"ACE: Tool change attempt {swap_attempt + 1} failed: {load_error}"
                    )

                    # If this was the last attempt, don't try recovery
                    if swap_attempt >= max_swap_attempts - 1:
                        raise

                    tried_tools.add(current_target_tool)

                    self.gcode.respond_info(
                        f"ACE: Attempting recovery - smart unload T{current_target_tool}..."
                    )

                    try:
                        to_inst_num = get_instance_from_tool(current_target_tool)
                        to_slot = get_local_slot(current_target_tool, to_inst_num)

                        if to_inst_num >= 0 and to_slot >= 0:
                            ace_inst = self.manager.instances[to_inst_num]
                            ace_inst._smart_unload_slot(
                                to_slot,
                                length=ace_inst.parkposition_to_toolhead_length,
                            )

                            ace_inst.inventory[to_slot]["status"] = "empty"
                            self.manager._sync_inventory_to_persistent(to_inst_num)
                            self.gcode.respond_info(f"ACE: Marked T{current_target_tool} as empty (failed swap)")

                    except Exception as unload_error:
                        self.gcode.respond_info(
                            f"ACE: Warning - recovery unload failed: {unload_error}"
                        )

                    self.gcode.respond_info("ACE: Searching for next matching spool...")

                    # Temporarily mark tried tools as unavailable during search
                    saved_statuses = {}
                    for tried_tool in tried_tools:
                        tried_inst_num = get_instance_from_tool(tried_tool)
                        tried_slot = get_local_slot(tried_tool, tried_inst_num)
                        if tried_inst_num >= 0 and tried_slot >= 0:
                            tried_ace = self.manager.instances[tried_inst_num]
                            if tried_ace:
                                saved_statuses[tried_tool] = tried_ace.inventory[tried_slot]["status"]
                                tried_ace.inventory[tried_slot]["status"] = "searching"  # Temp status

                    try:
                        next_tool = self.find_exact_match(from_tool)
                    finally:
                        # Restore original statuses
                        for tried_tool, saved_status in saved_statuses.items():
                            tried_inst_num = get_instance_from_tool(tried_tool)
                            tried_slot = get_local_slot(tried_tool, tried_inst_num)
                            if tried_inst_num >= 0 and tried_slot >= 0:
                                tried_ace = self.manager.instances[tried_inst_num]
                                if tried_ace:
                                    tried_ace.inventory[tried_slot]["status"] = saved_status

                    if next_tool == -1 or next_tool in tried_tools:
                        raise Exception(
                            f"No more matching spools available (already tried: {sorted(tried_tools)})"
                        )

                    self.gcode.respond_info(f"ACE: Found next candidate: T{next_tool}")
                    current_target_tool = next_tool

        except Exception as e:
            self.gcode.respond_info("ACE: *** ENDLESS SPOOL SWAP FAILED ***")
            self.gcode.respond_info(f"ACE: {e}")
            self.gcode.respond_info("ACE: Print is PAUSED - fix the issue and RESUME manually")

            # Show user prompt for failed swap
            from_inst_num = get_instance_from_tool(from_tool)
            from_slot = get_local_slot(from_tool, from_inst_num)

            material = "unknown"
            color = [0, 0, 0]

            if from_inst_num >= 0 and from_slot >= 0:
                ace_inst = self.manager.instances[from_inst_num]
                if ace_inst:
                    # Restore status for retry
                    ace_inst.inventory[from_slot]["status"] = "ready"
                    self.manager._sync_inventory_to_persistent(from_inst_num)

                    # Get material info for prompt
                    inv = ace_inst.inventory[from_slot]
                    material = inv.get("material", "unknown")
                    color = inv.get("color", [0, 0, 0])

            # Show failure prompt
            self._show_swap_failed_prompt(from_tool, from_inst_num, from_slot, material, color, str(e))

    def _show_swap_failed_prompt(self, tool_index, instance_num, local_slot, material, color, error_msg):
        """
        Show user prompt when endless spool swap fails.

        Args:
            tool_index: Original tool that ran out
            instance_num: ACE instance number
            local_slot: Local slot number
            material: Material type
            color: RGB color array
            error_msg: Error message explaining failure
        """
        self.gcode.run_script_from_command(
            'RESPOND TYPE=command MSG="action:prompt_begin Endless Spool Swap Failed"'
        )

        color_str = f"RGB({color[0]},{color[1]},{color[2]})"
        # Truncate error message to avoid overly long prompts
        short_error = error_msg.split('\n')[0][:100]

        prompt_text = (
            f"Endless spool swap failed for T{tool_index} (ACE {instance_num}, Slot {local_slot}) - "
            f"Material: {material}, Color: {color_str}. Error: {short_error}. "
            f"Please refill spool or load matching material, then RESUME."
        )

        self.gcode.run_script_from_command(
            f'RESPOND TYPE=command MSG="action:prompt_text {prompt_text}"'
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

    def get_status(self):
        """Return status dict for Klipper."""
        return {}
