# KS1 Custom Probe support (utilizing Klipper probe helpers)
#
# Copyright (C) 2025 Antiriad <mail.antiriad@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import logging
from . import probe

class ProbeKS1:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = config.get_name()
        
        self.speed = config.getfloat("speed", 5.0, above=0.0)
        self.lift_speed = config.getfloat("lift_speed", self.speed, above=0.0)
        self.x_offset = config.getfloat("x_offset", 0.0)
        self.y_offset = config.getfloat("y_offset", 0.0)
        self.z_offset = config.getfloat("z_offset", 0.0)

        # Currently not used/supported settings from printer.cfg
        # As this module uses probehelper for probing and not the manual aproach in this module
        # this settings are currently without any effect. They are left active here, so that
        # its still possible to load a standard KS1 probe config without getting klipper complaining
        # about this unused keys. 
        self.z_offset = config.getfloat("z_offset", 0.0)
        self.final_speed = config.getfloat("final_speed", 2.0, above=0.0)

        # Z position inference
        if config.has_section("stepper_z"):
            zconfig = config.getsection("stepper_z")
            self.z_position = zconfig.getfloat("position_min", 0.0)
        else:
            pconfig = config.getsection("printer")
            self.z_position = pconfig.getfloat("minimum_z_position", 0.0)
        
        self.z_position = config.getfloat("z_position", self.z_position)


        # CS1237 strain gauge sensor reference
        # Currently not used, as it enables itself and start reporting as soon as klippy connects
        #self.cs1237 = self.printer.lookup_object('cs1237')
        

        # Standard Klipper probe endstop (MCU handles triggering)
        self.mcu_endstop = probe.ProbeEndstopWrapper(config)

        # Standard probe methods
        self.get_mcu = self.mcu_endstop.get_mcu
        self.add_stepper = self.mcu_endstop.add_stepper
        self.get_steppers = self.mcu_endstop.get_steppers
        self.home_start = self.mcu_endstop.home_start
        self.home_wait = self.mcu_endstop.home_wait
        
        # Use MCU triggering
        self.query_endstop = self.mcu_endstop.query_endstop

        # Standard Klipper probe helpers
        self.cmd_helper = probe.ProbeCommandHelper(config, self, self.query_endstop)
        self.probe_offsets = probe.ProbeOffsetsHelper(config)
        self.param_helper = probe.ProbeParameterHelper(config)
        self.homing_helper = probe.HomingViaProbeHelper(config, self, self.param_helper)
        
        self.probe_session = probe.ProbeSessionHelper(
            config, self.param_helper, self.homing_helper.start_probe_session)
        
        # Register as the printer's probe object,so probe_k1 gets registred as standard probe
        config.get_printer().add_object('probe', self)
        
           
    # Interface for ProbeCommandHelper
    def get_probe_params(self, gcmd=None):
        return self.param_helper.get_probe_params(gcmd)

    def get_offsets(self):
        return self.probe_offsets.get_offsets()

    def get_status(self, eventtime):
        return self.cmd_helper.get_status(eventtime)

    def start_probe_session(self, gcmd):
        return self.probe_session.start_probe_session(gcmd)

    def get_position_endstop(self):
        return self.z_offset

    def get_lift_speed(self, gcmd=None):
        if gcmd is not None:
            return gcmd.get_float("LIFT_SPEED", self.lift_speed, above=0.0)
        return self.lift_speed

    # Probe lifecycle methods
    def probe_prepare(self, hmove):
        """Ensure endstop isn't already triggered before probing."""
        toolhead = self.printer.lookup_object("toolhead")
        reactor = self.printer.get_reactor()

        # Ask MCU slightly IN THE FUTURE to avoid stale/latched state
        def _is_endstop_active():
            pt = toolhead.get_last_move_time() + 0.050  # 50 ms in the future
            try:
                return bool(self.mcu_endstop.query_endstop(pt))
            except Exception:
                return False

        # Debounced check: read twice with a short gap
        pre_trig_1 = _is_endstop_active()
        if pre_trig_1:
            reactor.pause(reactor.monotonic() + 0.030)  # 30 ms settle
            pre_trig_2 = _is_endstop_active()
        else:
            pre_trig_2 = False

        if pre_trig_1 and pre_trig_2:
            logging.warning("ProbeKS1: endstop TRIGGERED before move; lifting 2mm to clear")
            cur = toolhead.get_position()
            toolhead.manual_move([cur[0], cur[1], cur[2] + 2.0], self.get_lift_speed(None))
            toolhead.wait_moves()
            # Re-check once more after lift
            if _is_endstop_active():
                raise self.printer.command_error(
                    "ProbeKS1: Endstop still triggered before probing after 2mm lift "
                )

        # Delegate to standard probe preparation
        self.mcu_endstop.probe_prepare(hmove)

    def probe_finish(self, hmove):
        self.mcu_endstop.probe_finish(hmove)

    def multi_probe_begin(self):
        self.mcu_endstop.multi_probe_begin()

    def multi_probe_end(self):
        self.mcu_endstop.multi_probe_end()

    def raise_probe(self):
        # No-op for strain gauge probe (always active)
        pass

    def lower_probe(self):
        # No-op for strain gauge probe (always active)
        pass

    def move(self, coord, speed):
        self.printer.lookup_object("toolhead").manual_move(coord, speed)

def load_config(config):
    return ProbeKS1(config)
