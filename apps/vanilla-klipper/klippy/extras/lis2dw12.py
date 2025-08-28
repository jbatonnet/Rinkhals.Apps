# Support for reading acceleration data from an LIS2DW12 chip
#
# Copyright (C) 2025 Antiriad <mail.antiriad@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import logging
from . import bus, bulk_sensor, adxl345

# LIS2DW12 registers and constants
LIS2DW12_REGISTERS = {
    "REG_DEVID": 0x0F,
    "REG_CTRL1": 0x20,
    "REG_CTRL6": 0x25,
    "REG_FIFO_CTRL": 0x2E,
    "REG_MOD_READ": 0x80,
}

LIS2DW12_INFO = {
    "DEV_ID": 0x44,
    "POWER_OFF": 0x00,
    "SET_CTRL1_MODE": 0x04,
    "SET_FIFO_CTL": 0xC0,
    "SET_CTRL6_ODR_FS": 0x04,
    "FREEFALL_ACCEL": 9.80665 * 1000.,
    "SCALE_XY": 0.000244140625 * 9.80665 * 1000,
    "SCALE_Z": 0.000244140625 * 9.80665 * 1000,
}

LIS2DW12_QUERY_RATES = {
    25: 0x3, 50: 0x4, 100: 0x5, 200: 0x6, 400: 0x7, 800: 0x8, 1600: 0x9,
}

BYTES_PER_SAMPLE = 6
SAMPLES_PER_BLOCK = 8
MIN_MSG_TIME = 0.100


class LIS2DW12:
    def __init__(self, config):
        self.printer = config.get_printer()
        adxl345.AccelCommandHelper(config, self)

        logging.info(
            "LIS2DW12: Initializing section '%s'", config.get_name()
        )
        logging.info(
            "LIS2DW12: axes_map=%s, rate=%s",
            config.get("axes_map", "x,y,z"),
            config.get("rate", 1600),
        )

        # Axes map and scaling
        am = {
            "x": (0, LIS2DW12_INFO["SCALE_XY"]),
            "y": (1, LIS2DW12_INFO["SCALE_XY"]),
            "z": (2, LIS2DW12_INFO["SCALE_Z"]),
            "-x": (0, -LIS2DW12_INFO["SCALE_XY"]),
            "-y": (1, -LIS2DW12_INFO["SCALE_XY"]),
            "-z": (2, -LIS2DW12_INFO["SCALE_Z"]),
        }
        axes_map = config.getlist("axes_map", ["x", "y", "z"], ',', count=3)
        self.axes_map = [am[str(a).strip()] for a in axes_map]

        self.data_rate = config.getint("rate", 1600)
        if self.data_rate not in LIS2DW12_QUERY_RATES:
            raise config.error("Invalid lis2dw12 rate parameter")

        # Setup SPI bus
        self.bus = bus.MCU_SPI_from_config(config, 3, default_speed=5000000)
        self.mcu = self.bus.get_mcu()
        self.oid = self.mcu.create_oid()
        self.last_sequence = 0
        self.max_query_duration = 1 << 31

        # MCU commands
        config_cmd = (
            f"config_lis2dw12 oid={self.oid} spi_oid={self.bus.get_oid()}"
        )
        self.mcu.add_config_cmd(config_cmd)
        rest_ticks = self.mcu.seconds_to_clock(4.0 / self.data_rate)
        query_cmd = (
            f"query_lis2dw12 oid={self.oid} clock=0 rest_ticks={rest_ticks}"
        )
        self.mcu.add_config_cmd(query_cmd, on_restart=True)
        self.mcu.register_config_callback(self._build_config)
        self.mcu.register_response(
            self._handle_lis2dw12_data, "lis2dw12_data", self.oid
        )
        self.mcu.register_response(
            self._handle_lis2dw12_status, "lis2dw12_status", self.oid
        )

        # Batch/bulk sensor setup
        self.samples = []
        self.last_error_count = 0
        self.last_limit_count = 0
        self.name = config.get_name().split()[-1]
        BATCH_UPDATES = 0.100
        self.batch_bulk = bulk_sensor.BatchBulkHelper(
            self.printer,
            self._process_batch,
            self._start_measurements,
            self._finish_measurements,
            BATCH_UPDATES,
        )
        hdr = ("time", "x_acceleration", "y_acceleration", "z_acceleration")
        self.batch_bulk.add_mux_endpoint(
            "lis2dw12/dump_lis2dw12", "sensor", self.name, {"header": hdr}
        )
        self.clock_sync = bulk_sensor.ClockSyncRegression(self.mcu, 640)

    def _build_config(self):
        cmdqueue = self.bus.get_command_queue()
        self.query_sensor_cmd = self.mcu.lookup_command(
            "query_lis2dw12 oid=%c clock=%u rest_ticks=%u", cq=cmdqueue
        )
        self.query_sensor_status_cmd = self.mcu.lookup_query_command(
            "query_lis2dw12_status oid=%c",
            "lis2dw12_status oid=%c clock=%u query_ticks=%u next_sequence=%hu"
            " buffered=%c fifo=%c limit_count=%hu",
            self.oid,
            cmdqueue,
        )

    def start_internal_client(self):
        aqh = adxl345.AccelQueryHelper(self.printer)
        self.batch_bulk.add_client(aqh.handle_batch)
        return aqh

    def _handle_lis2dw12_data(self, params):
        self.samples.append(params)

    def _handle_lis2dw12_status(self, params):
        self.last_error_count = params.get("errors", self.last_error_count)
        self.last_limit_count = params.get("limit_count", self.last_limit_count)

    def _process_batch(self, eventtime):
        self._update_clock()
        raw_samples = self.samples
        self.samples = []
        if not raw_samples:
            # No data available, return empty dict (like adxl345)
            return {}
        try:
            samples = self.extract_samples(raw_samples)
        except Exception:
            samples = []
        return {
            "data": samples,
            "errors": self.last_error_count,
            "overflows": self.last_limit_count,
        }

    def _start_measurements(self):
        dev_id = self.read_reg(LIS2DW12_REGISTERS["REG_DEVID"])
        if dev_id != LIS2DW12_INFO["DEV_ID"]:
            raise self.printer.command_error(
                "Invalid lis2dw12 id (got %x vs %x)"
                % (dev_id, LIS2DW12_INFO["DEV_ID"])
            )

        ctrl1_val = (
            LIS2DW12_QUERY_RATES[self.data_rate] << 4
            | LIS2DW12_INFO["SET_CTRL1_MODE"]
        )
        self.set_reg(LIS2DW12_REGISTERS["REG_CTRL1"], ctrl1_val)
        self.set_reg(
            LIS2DW12_REGISTERS["REG_FIFO_CTRL"], LIS2DW12_INFO["POWER_OFF"]
        )
        self.set_reg(
            LIS2DW12_REGISTERS["REG_CTRL6"], LIS2DW12_INFO["SET_CTRL6_ODR_FS"]
        )
        self.set_reg(
            LIS2DW12_REGISTERS["REG_FIFO_CTRL"], LIS2DW12_INFO["SET_FIFO_CTL"]
        )

        # Start bulk reading
        systime = self.printer.get_reactor().monotonic()
        print_time = self.mcu.estimated_print_time(systime) + MIN_MSG_TIME
        reqclock = self.mcu.print_time_to_clock(print_time)
        rest_ticks = self.mcu.seconds_to_clock(4.0 / self.data_rate)
        self.query_sensor_cmd.send([self.oid, reqclock, rest_ticks])

        # Reset & cleanup state here
        self.samples = []
        self.last_sequence = 0
        self.last_error_count = 0
        self.last_limit_count = 0
        self.max_query_duration = 1 << 31
        if hasattr(self.clock_sync, "reset"):
            self.clock_sync.reset(float(reqclock), 0.0)

    def _finish_measurements(self):
        self.set_reg(LIS2DW12_REGISTERS["REG_FIFO_CTRL"], 0x00)
        self.query_sensor_cmd.send_wait_ack([self.oid, 0, 0])
        self.set_reg(LIS2DW12_REGISTERS["REG_FIFO_CTRL"], 0x00)

    def read_reg(self, reg):
        params = self.bus.spi_transfer(
            [reg | LIS2DW12_REGISTERS["REG_MOD_READ"], 0x00]
        )
        response = bytearray(params["response"])
        return response[1]

    def set_reg(self, reg, val, minclock=0):
        self.bus.spi_send([reg, val & 0xFF], minclock=minclock)
        stored_val = self.read_reg(reg)
        if stored_val != val:
            raise self.printer.command_error(
                "Failed to set LIS2DW12 register [0x%x] to 0x%x: got 0x%x."
                % (reg, val, stored_val)
            )

    def extract_samples(self, raw_samples):
        x_pos, x_scale = self.axes_map[0]
        y_pos, y_scale = self.axes_map[1]
        z_pos, z_scale = self.axes_map[2]

        try:
            time_base, chip_base, inv_freq = self.clock_sync.get_time_translation()
        except ZeroDivisionError:
            raise self.printer.command_error(
                "LIS2DW12: Clock synchronization not ready. "
                "Not enough data for timing translation. "
                "Wait for more samples or check sensor connection."
            )

        count = 0
        samples = [None] * (len(raw_samples) * SAMPLES_PER_BLOCK)

        for params in raw_samples:
            seq_diff = (self.last_sequence - params.get("sequence", 0)) & 0xFFFF
            seq_diff -= (seq_diff & 0x8000) << 1
            seq = self.last_sequence - seq_diff
            d = params.get("data", b"")

            msg_cdiff = float(seq) * float(SAMPLES_PER_BLOCK) - chip_base

            for i in range(0, len(d), BYTES_PER_SAMPLE):
                if i + BYTES_PER_SAMPLE > len(d):
                    break

                xlow = d[i] & 0xFC  # Mask bottom 2 bits
                ylow = d[i + 1] & 0xFC
                zlow = d[i + 2] & 0xFC
                xhigh = d[i + 3] & 0xFF
                yhigh = d[i + 4] & 0xFF
                zhigh = d[i + 5] & 0xFF

                rx = xlow | (xhigh << 8)
                ry = ylow | (yhigh << 8)
                rz = zlow | (zhigh << 8)

                # Convert to signed 16-bit
                rx = rx if rx < 0x8000 else rx - 0x10000
                ry = ry if ry < 0x8000 else ry - 0x10000
                rz = rz if rz < 0x8000 else rz - 0x10000

                raw_xyz = [rx, ry, rz]

                # Apply axes mapping and scaling
                x = raw_xyz[int(x_pos)] * x_scale / 4.0
                y = raw_xyz[int(y_pos)] * y_scale / 4.0
                z = raw_xyz[int(z_pos)] * z_scale / 4.0

                # Sample index within this message
                sample_offset = float(i // BYTES_PER_SAMPLE)
                ptime = time_base + (msg_cdiff + sample_offset) * inv_freq

                samples[count] = (ptime, x, y, z)
                count += 1

        del samples[count:]
        return samples

    def _update_clock(self, minclock=0):
        # Query with retry logic
        for retry in range(5):
            try:
                params = self.query_sensor_status_cmd.send([self.oid], minclock)
                fifo = params.get("fifo", 0) & 0x7F
                if fifo <= 32:
                    break
            except Exception as e:
                if retry == 4:
                    raise self.printer.command_error(
                        "Unable to query lis2dw12 fifo: %s" % str(e)
                    )
                continue

        # Update sequence tracking
        mcu_clock = self.mcu.clock32_to_clock64(params["clock"])
        sequence = (self.last_sequence & (~0xFFFF)) | params["next_sequence"]
        if sequence < self.last_sequence:
            sequence += 0x10000
        self.last_sequence = sequence

        # Update limit count tracking
        buffered = params.get("buffered", 0)
        limit_count = ((self.last_limit_count & (~0xFFFF)) | params["limit_count"])
        if limit_count < self.last_limit_count:
            limit_count += 0x10000
        self.last_limit_count = limit_count

        # Duration-based filtering
        duration = params["query_ticks"]
        if duration > self.max_query_duration:
            self.max_query_duration = max(
                2 * self.max_query_duration, self.mcu.seconds_to_clock(0.000005)
            )
            return

        self.max_query_duration = 2 * duration

        msg_count = (
            sequence * SAMPLES_PER_BLOCK
            + buffered // BYTES_PER_SAMPLE
            + fifo
        )
        chip_clock = float(msg_count + 1)  # +1 for timing offset like Go

        self.clock_sync.update(float(mcu_clock + duration // 2), chip_clock)

        # Set last chip clock for timing continuity
        if hasattr(self.clock_sync, "set_last_chip_clock"):
            self.clock_sync.set_last_chip_clock(chip_clock)


def load_config(config):
    return LIS2DW12(config)


def load_config_prefix(config):
    return LIS2DW12(config)
