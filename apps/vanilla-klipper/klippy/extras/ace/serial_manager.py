"""
AceSerialManager: Handles all serial communication with ACE Pro units.

Responsibilities:
- Serial port connect/disconnect
- Request/response queueing with sliding window
- CRC calculation and frame parsing
- Callback dispatch
- Port detection and enumeration
"""

import serial
import json
import struct
import threading
import queue
import logging
import traceback
import re
from serial import SerialException
import serial.tools.list_ports


class AceSerialManager:
    """Manages serial communication with a single ACE Pro unit."""

    QUEUE_MAXSIZE = 1024
    WINDOW_SIZE = 4
    DEFAULT_TIMEOUT_S = 5.0

    def __init__(
            self,
            gcode,
            reactor,
            instance_num=0,
            ace_enabled=True,
            status_debug_logging=False,
            supervision_enabled=True):
        """
        Initialize serial manager.

        Args:
            gcode: Klipper gcode object
            reactor: Klipper reactor for async operations
            instance_num: ACE instance number for logging
            ace_enabled: Initial ACE Pro enabled state
            status_debug_logging: Enable detailed status logging for debugging
            supervision_enabled: Enable communication health supervision
        """
        self._port = None
        self._usb_location = None
        self._baud = None

        self.gcode = gcode
        self.reactor = reactor
        self.instance_num = instance_num

        self._serial = None
        self._connected = False
        self._lock = threading.RLock()
        self._serial_lock = threading.Lock()

        self._request_id = 0
        self._callback_map = {}
        self.inflight = {}

        self._hp_queue = queue.Queue(maxsize=self.QUEUE_MAXSIZE)
        self._queue = queue.Queue(maxsize=self.QUEUE_MAXSIZE)

        self.read_buffer = bytearray()
        self.send_time = None

        self.writer_timer = None
        self.reader_timer = None
        self.heartbeat_timer = None
        self.connect_timer = None

        self._last_status_request_time = 0
        self.heartbeat_interval = 1.0
        self.heartbeat_callback = None
        self.on_connect_callback = None

        self.timeout_s = self.DEFAULT_TIMEOUT_S
        self.timeout_multiplier = 2

        self.last_status = None
        self.last_action = None
        self.last_slot_states = {}
        self.last_slot_payloads = {}
        self.last_dryer_status = None
        self.last_temp = None
        self.last_feed_assist_count = None
        self.last_cont_assist_time = None

        self._ace_pro_enabled = ace_enabled
        self._status_debug_logging = bool(status_debug_logging)
        self._supervision_enabled = bool(supervision_enabled)

        # Connection stability tracking
        # Rate-based detection: unstable if too many reconnects in short window
        self.INSTABILITY_WINDOW = 180.0      # Look at reconnects in last 3 minutes
        self.INSTABILITY_THRESHOLD = 6       # 6+ reconnects in window = unstable
        self.STABILITY_GRACE_PERIOD = 30.0   # Must stay connected 30s to be "stable"
        self.COUNTER_RESET_PERIOD = 180.0    # Reset counter after 3 min of stability

        self._reconnect_timestamps = []      # List of monotonic times of reconnect attempts
        self._last_connected_time = 0.0      # Monotonic time of last successful connect
        self._counter_reset_time = 0.0       # Time when counter was last reset
        self._reconnect_backoff = 5.0        # Current backoff delay (increases on failure)
        self.RECONNECT_BACKOFF_MIN = 5.0     # Minimum backoff delay
        self.RECONNECT_BACKOFF_MAX = 30.0    # Maximum backoff delay (30 seconds)
        self.RECONNECT_BACKOFF_FACTOR = 1.5  # Multiply backoff on each failure
        self.TOPOLOGY_RELEARN_THRESHOLD = 1  # After N mismatches, accept new topology

        # Expected topology positions for validation
        self._expected_topology_positions = None  # Set after first successful enumeration
        self._topology_validation_failed_count = 0

        # Communication health supervision
        # Track timeouts and unsolicited messages to detect out-of-sync communication
        self.COMM_SUPERVISION_WINDOW = 30.0     # Monitor last 30 seconds
        self.COMM_TIMEOUT_THRESHOLD = 15        # 15+ timeouts in window (AND condition)
        self.COMM_UNSOLICITED_THRESHOLD = 15    # 15+ unsolicited in window (AND condition)
        self._comm_timeout_timestamps = []      # List of timeout event times
        self._comm_unsolicited_timestamps = []  # List of unsolicited message times
        self._last_supervision_check = 0.0      # Last time we checked health
        self.SUPERVISION_CHECK_INTERVAL = 5.0   # Check every 5 seconds

    def enable_ace_pro(self):
        """Enable ACE Pro and reconnect if not connected."""
        was_disabled = not self._ace_pro_enabled
        self._ace_pro_enabled = True

        if was_disabled:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: ACE Pro enabled - reconnecting"
            )
            baud = self._baud if self._baud else 115200
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Using baud rate: {baud}"
            )
            self.connect_to_ace(baud, delay=0.5)

    def disable_ace_pro(self):
        """Disable ACE Pro and disconnect immediately."""
        self._ace_pro_enabled = False
        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: ACE Pro disabled - disconnecting"
        )
        self.disconnect()

    def is_ace_pro_enabled(self):
        """Check if ACE Pro is enabled."""
        return self._ace_pro_enabled

    # ========== Serial Port Detection ==========

    def _parse_usb_location(self, location_str):
        """
        Parse USB location string into tuple for natural sorting.

        Examples:
            "1-1.4.3:1.0" → (1, 1, 4, 3)
            "acm.2" → (999998, 2)

        ACM fallback locations sort after USB locations but before
        unrecognized devices (999999).
        """
        if not location_str:
            return (999999,)

        location_str = str(location_str)

        # Handle ACM fallback format (e.g., "acm.2")
        if location_str.startswith('acm.'):
            try:
                acm_num = int(location_str[4:])
                return (999998, acm_num)  # Sort after USB, before unknown
            except ValueError:
                return (999999,)

        # Strip interface suffix (e.g., ":1.0")
        location_str = location_str.split(':')[0]
        parts = location_str.replace('-', '.').split('.')

        try:
            return tuple(int(p) for p in parts)
        except ValueError:
            return (999999,)

    def find_com_port(self, device_name, instance=0):
        """
        Find serial port for device, sorted by USB topology.

        Returns the nth matching port (instance index) sorted by USB location,
        ensuring consistent ordering across hot-plugs.

        Args:
            device_name: Device identifier in port description
            instance: Which matching port to return (0=first, 1=second, etc)

        Returns:
            str: Serial device path or None if not found
        """
        matches = []

        for portinfo in serial.tools.list_ports.comports():
            if device_name not in portinfo.description:
                continue

            # Extract USB location from hwid
            location = None
            m = re.search(r'LOCATION=([-\w\.]+)', portinfo.hwid)
            if m:
                location = m.group(1)
            else:
                # Fallback: extract ACM number
                m2 = re.search(r'ACM(\d+)', portinfo.device)
                if m2:
                    location = f"acm.{m2.group(1)}"
                else:
                    location = portinfo.device

            sort_key = self._parse_usb_location(location)
            matches.append((sort_key, location, portinfo.device))

            logging.info(
                f"ACE[{self.instance_num}] USB device found: {portinfo.device} "
                f"at location '{location}' (sort_key={sort_key})"
            )

        # Sort by location
        matches.sort(key=lambda x: x[0])

        if matches:
            # Store expected topology positions for validation
            if self._expected_topology_positions is None:
                # Check that we found at least instance+1 devices
                if len(matches) < instance + 1:
                    self.gcode.respond_info(
                        f"ACE[{self.instance_num}]: WARNING - Only {len(matches)} ACE(s) found, "
                        f"but instance {instance} requires at least {instance + 1}. "
                        f"Waiting for all ACEs to enumerate..."
                    )
                    return None

                self._expected_topology_positions = [self._parse_usb_location(loc) for _, loc, _ in matches]
                logging.info(
                    f"ACE[{self.instance_num}]: Stored topology positions: {self._expected_topology_positions}"
                )

            # Prefer a port whose topology matches the stored signature for this instance
            selected_dev = None
            if self._expected_topology_positions and instance < len(self._expected_topology_positions):
                expected_topo = self._expected_topology_positions[instance]
                for sort_key, loc, dev in matches:
                    if sort_key == expected_topo:
                        selected_dev = dev
                        break

            logging.info(
                f"ACE[{self.instance_num}] USB enumeration order:"
            )
            for idx, (sort_key, loc, dev) in enumerate(matches):
                marker = ""
                if selected_dev and dev == selected_dev:
                    marker = " <- SELECTED (topology match)"
                elif idx == instance and not selected_dev:
                    marker = " <- SELECTED"
                logging.info(
                    f"  [{idx}] {dev} at {loc}{marker}"
                )

            if selected_dev:
                return selected_dev

        if len(matches) > instance:
            return matches[instance][2]
        return None

    def _get_usb_location_for_port(self, port):
        """Get USB location string for a specific port."""
        for portinfo in serial.tools.list_ports.comports():
            if portinfo.device == port:
                m = re.search(r'LOCATION=([-\w\.]+)', portinfo.hwid)
                if m:
                    return m.group(1)
                # Fallback
                m2 = re.search(r'ACM(\d+)', portinfo.device)
                if m2:
                    return f"acm.{m2.group(1)}"
                return portinfo.device
        return None

    def get_usb_location(self):
        """Get current USB location."""
        return getattr(self, '_usb_location', None)

    def get_usb_topology_position(self):
        """
        Get normalized topology position (depth in daisy chain).
        Returns the number of hops from root, ignoring which root port.

        Examples:
            "2-2.3" -> 2 (root -> hub -> port)
            "2-2.4.3" -> 3 (root -> hub -> port -> port)
            "1-3.2" -> 2 (root -> port -> port)
        """
        location = self.get_usb_location()
        if not location:
            return None

        # Count the number of dots/hyphens = depth in USB tree
        # Strip the controller number prefix (before first hyphen)
        if '-' in location:
            topo = location.split('-', 1)[1]  # e.g., "2.3" or "2.4.3"
            depth = topo.count('.') + 1  # Count ports in chain
            return depth

        return None

    def _validate_topology_position(self, instance):
        """
        Validate that this instance is connected to the correct physical ACE.
        Uses USB topology depth to verify position in daisy chain.

        Returns:
            bool: True if topology position is correct, False otherwise
        """
        current_topo = self._parse_usb_location(self._usb_location)

        if self._expected_topology_positions is None:
            # First connection - store it but validate the order is ascending
            # Instance 0 should be shallowest (closest to root), instance 1 deeper, etc.
            # We can't fully validate yet, but we can check relative to what we have
            logging.info(
                f'ACE[{instance}]: First connection - storing topology {current_topo}'
            )
            return True

        if instance >= len(self._expected_topology_positions):
            # Instance number out of range - pad the list with None
            while len(self._expected_topology_positions) <= instance:
                self._expected_topology_positions.append(None)
            return True

        expected_topo = self._expected_topology_positions[instance]

        # If no expected topology (padded with None), accept without validation
        if expected_topo is None:
            return True

        # Compare topology positions (they should match)
        if current_topo != expected_topo:
            self._topology_validation_failed_count += 1
            self.gcode.respond_info(
                f'ACE[{instance}]: Topology mismatch - '
                f'expected {expected_topo}, got {current_topo} '
                f'(failure #{self._topology_validation_failed_count})'
            )
            if self._topology_validation_failed_count >= self.TOPOLOGY_RELEARN_THRESHOLD:
                # Clear all topology expectations to force re-enumeration
                self._expected_topology_positions = None
                self.gcode.respond_info(
                    f'ACE[{instance}]: Topology expectations cleared after '
                    f'{self._topology_validation_failed_count} failures - will re-enumerate'
                )
                self._topology_validation_failed_count = 0
                return False  # Fail this connection attempt to trigger re-enumeration
            return False

        # Reset failure counter on success
        self._topology_validation_failed_count = 0
        return True

    # ========== Serial Connection Management ==========

    def connect_to_ace(self, baud, delay=2):
        """Start connection attempts (only if ACE enabled)."""
        if not self._ace_pro_enabled:
            self.gcode.respond_info(
                f'ACE[{self.instance_num}]: ACE Pro disabled - '
                f'not starting connection attempts'
            )
            return

        self._baud = baud

        def connect_callback(eventtime):
            if not self._ace_pro_enabled:
                self.gcode.respond_info(
                    f'ACE[{self.instance_num}]: ACE Pro disabled during connection attempt'
                )
                return self.reactor.NEVER

            if self.auto_connect(self.instance_num, self._baud):
                logging.info(f'ACE[{self.instance_num}]: Connected')
                # Reset backoff on successful connect
                self._reconnect_backoff = self.RECONNECT_BACKOFF_MIN
                return self.reactor.NEVER
            else:
                # Track failed connection attempt for stability detection
                # (only track failures, not the initial attempt)
                now = self.reactor.monotonic()
                self._reconnect_timestamps.append(now)

                # Prune old timestamps outside the instability window
                cutoff = now - self.INSTABILITY_WINDOW
                self._reconnect_timestamps = [t for t in self._reconnect_timestamps if t > cutoff]

                # Increase backoff delay on failure (exponential backoff)
                current_backoff = self._reconnect_backoff
                next_backoff = self._reconnect_backoff * self.RECONNECT_BACKOFF_FACTOR
                if next_backoff >= self.RECONNECT_BACKOFF_MAX:
                    # Reset to min after hitting max (cyclic backoff)
                    self._reconnect_backoff = self.RECONNECT_BACKOFF_MIN
                else:
                    self._reconnect_backoff = next_backoff
                recent_count = len(self._reconnect_timestamps)
                self.gcode.respond_info(
                    f'ACE[{self.instance_num}]: Retry in {current_backoff:.0f}s '
                    f'({recent_count} attempts in last {int(self.INSTABILITY_WINDOW)}s)'
                )
                return eventtime + current_backoff

        initial_delay = self._reconnect_backoff
        logging.info(
            f'ACE[{self.instance_num}]: Starting connection (first attempt in {initial_delay:.0f}s)'
        )
        self.connect_timer = self.reactor.register_timer(
            connect_callback,
            self.reactor.monotonic() + initial_delay
        )

    def reconnect(self, delay=None):
        """Disconnect and schedule reconnection (only if ACE enabled)."""
        if not self._ace_pro_enabled:
            self.gcode.respond_info(
                f'ACE[{self.instance_num}]: ACE Pro disabled - not reconnecting'
            )
            return

        # Get current reconnect count for logging (don't add timestamp here - callback does it on failure)
        now = self.reactor.monotonic()
        cutoff = now - self.INSTABILITY_WINDOW
        self._reconnect_timestamps = [t for t in self._reconnect_timestamps if t > cutoff]

        recent_count = len(self._reconnect_timestamps)
        self.gcode.respond_info(
            f'ACE[{self.instance_num}]: (Re)connecting '
            f'({recent_count} reconnects in last {int(self.INSTABILITY_WINDOW)}s)'
        )
        self.disconnect()

        # Use provided delay parameter, or default to current backoff
        initial_delay = delay if delay is not None else self._reconnect_backoff
        self.gcode.respond_info(f'ACE[{self.instance_num}]: Scheduling reconnect in {initial_delay:.0f}s')

        def _reconnect_callback(eventtime):
            if not self._ace_pro_enabled:
                self.gcode.respond_info(
                    f'ACE[{self.instance_num}]: ACE Pro disabled during reconnect attempt'
                )
                return self.reactor.NEVER

            if self.auto_connect(self.instance_num, self._baud):
                self.gcode.respond_info(f'ACE[{self.instance_num}]: Connected')
                # Reset backoff on successful connect
                self._reconnect_backoff = self.RECONNECT_BACKOFF_MIN
                return self.reactor.NEVER
            else:
                # Track failed connection attempt for stability detection
                now = self.reactor.monotonic()
                self._reconnect_timestamps.append(now)

                # Prune old timestamps outside the instability window
                cutoff = now - self.INSTABILITY_WINDOW
                self._reconnect_timestamps = [t for t in self._reconnect_timestamps if t > cutoff]

                # Increase backoff delay on failure (exponential backoff)
                current_backoff = self._reconnect_backoff
                next_backoff = self._reconnect_backoff * self.RECONNECT_BACKOFF_FACTOR
                if next_backoff >= self.RECONNECT_BACKOFF_MAX:
                    # Reset to min after hitting max (cyclic backoff)
                    self._reconnect_backoff = self.RECONNECT_BACKOFF_MIN
                else:
                    self._reconnect_backoff = next_backoff
                recent_count = len(self._reconnect_timestamps)
                self.gcode.respond_info(
                    f'ACE[{self.instance_num}]: Retry in {current_backoff:.0f}s '
                    f'({recent_count} attempts in last {int(self.INSTABILITY_WINDOW)}s)'
                )
                return eventtime + current_backoff

        self.gcode.respond_info(
            f'ACE[{self.instance_num}]: Scheduling reconnect in {initial_delay:.0f}s'
        )
        self.connect_timer = self.reactor.register_timer(
            _reconnect_callback,
            self.reactor.monotonic() + initial_delay
        )

    def ensure_connect_timer(self):
        """Ensure a reconnect timer is scheduled if disconnected."""
        if self._ace_pro_enabled and not self.is_connected() and self.connect_timer is None:
            self.gcode.respond_info(
                f'ACE[{self.instance_num}]: No active connect timer, scheduling reconnect'
            )
            self.reconnect(self._reconnect_backoff)

    def dwell(self, delay=1.0):
        """Sleep in reactor time."""
        currTs = self.reactor.monotonic()
        self.reactor.pause(currTs + delay)

    def auto_connect(self, instance, baud):
        """Attempt to connect to ACE device."""
        port = self.find_com_port('ACE', instance)
        if port is None:
            self.gcode.respond_info(f'ACE[{instance}]: No ACE device found')
            return False

        self._port = port
        self._baud = baud
        self._usb_location = self._get_usb_location_for_port(port)

        logging.info('Try connecting to ' + str(port))
        connected = self.connect(port, baud)
        self.serial_name = port

        if not connected:
            self.gcode.respond_info(
                f'ACE[{instance}]: auto_connect: Failed to connect to {port}, retrying in 1s'
            )
            return False

        logging.info(
            f'ACE[{instance}]: auto_connect: Connected to {port}, sending get_info request'
        )

        # Validate we're connected to the correct physical ACE
        if not self._validate_topology_position(instance):
            self.gcode.respond_info(
                f'ACE[{instance}]: Topology validation failed - disconnecting and retrying'
            )
            self.disconnect()
            return False

        self.send_request(
            request={"method": "get_info"},
            callback=lambda response: self._log_info_response(response)
        )

        return True

    def _log_info_response(self, response):
        """
        Log get_info response with port and USB topology context.
        """
        port = self.serial_name or self._port or "unknown"
        topo = self._usb_location or "unknown"
        self.gcode.respond_info(
            f"ACE[{self.instance_num}]: {response} (port={port}, usb={topo})"
        )

    def connect(self, port, baud):
        """
        Connect to serial device.

        Args:
            port: Serial port path (e.g., "/dev/ttyACM0")
            baud: Baud rate

        Returns:
            bool: True if successfully connected
        """
        try:
            self._serial = serial.Serial(
                port=port,
                baudrate=baud,
                timeout=0,
                write_timeout=0.1
            )
            if self._serial.is_open:
                self._connected = True
                logging.info(f'ACE[{self.instance_num}]: Serial port {port} opened')
                # DON'T reset _request_id on reconnect - old responses may still arrive
                # Resetting to 0 would cause ID collisions with stale ACE responses

                # Flush buffers to discard any stale data from previous session
                self._serial.reset_input_buffer()
                self._serial.reset_output_buffer()

                if self.writer_timer is None:
                    self.writer_timer = self.reactor.register_timer(self._writer, self.reactor.NOW)
                if self.reader_timer is None:
                    self.reader_timer = self.reactor.register_timer(self._reader, self.reactor.NOW)

                if self.connect_timer is not None:
                    self.reactor.unregister_timer(self.connect_timer)
                    self.connect_timer = None

                self.start_heartbeat()

                # Record connection time for stability grace period tracking
                self._last_connected_time = self.reactor.monotonic()

                # Clear supervision counters on successful connection
                self._comm_timeout_timestamps = []
                self._comm_unsolicited_timestamps = []

                # Call on_connect callback if registered
                if self.on_connect_callback:
                    try:
                        self.on_connect_callback()
                    except Exception as e:
                        logging.warning(
                            f"ACE[{self.instance_num}]: on_connect callback error: {e}"
                        )

                return True
        except SerialException as e:
            self.gcode.respond_info(f"ACE[{self.instance_num}]: Connection failed: {e}")
            self._serial = None
        return False

    def disconnect(self):
        """Close serial connection and stop all timers."""
        self.stop_heartbeat()

        if self._serial and self._serial.is_open:
            try:
                self._serial.close()
            except Exception as e:
                logging.error(f"ACE[{self.instance_num}]: Error closing serial: {e}")

        self._connected = False
        self.read_buffer = bytearray()
        self.clear_queues()

        # Clear supervision counters on disconnect
        self._comm_timeout_timestamps = []
        self._comm_unsolicited_timestamps = []

        # Stop writer timer
        if self.writer_timer:
            try:
                self.reactor.unregister_timer(self.writer_timer)
            except Exception:
                pass
            self.writer_timer = None

        # Stop reader timer
        if self.reader_timer:
            try:
                self.reactor.unregister_timer(self.reader_timer)
            except Exception:
                pass
            self.reader_timer = None

        if self.connect_timer:
            try:
                self.reactor.unregister_timer(self.connect_timer)
            except Exception:
                pass
            self.connect_timer = None

        logging.info(
            f"ACE[{self.instance_num}]: Disconnected - all timers stopped"
        )

    def is_connected(self):
        """Check if serial connection is active."""
        return self._connected and self._serial and self._serial.is_open

    def _get_recent_reconnect_count(self):
        """
        Get number of reconnects within the instability window.

        Also prunes old timestamps and resets counter after stability period.
        """
        now = self.reactor.monotonic()

        # Prune old timestamps
        cutoff = now - self.INSTABILITY_WINDOW
        self._reconnect_timestamps = [t for t in self._reconnect_timestamps if t > cutoff]

        # Reset counter after extended stability period
        if (self._last_connected_time > 0 and
                (now - self._last_connected_time) > self.COUNTER_RESET_PERIOD and
                len(self._reconnect_timestamps) == 0):
            if self._counter_reset_time < self._last_connected_time:
                self._counter_reset_time = now

        return len(self._reconnect_timestamps)

    def is_connection_stable(self):
        """
        Check if connection is stable using rate-based detection.

        Stable means:
        - Currently connected
        - Connected for at least STABILITY_GRACE_PERIOD (30s)
        - Less than INSTABILITY_THRESHOLD (3) reconnects in INSTABILITY_WINDOW (60s)

        Returns:
            bool: True if connected and stable
        """
        if not self.is_connected():
            return False

        now = self.reactor.monotonic()

        # Check grace period: must be connected for at least 30 seconds
        time_connected = now - self._last_connected_time
        if time_connected < self.STABILITY_GRACE_PERIOD:
            return False

        # Check reconnect rate: less than threshold in window
        recent_count = self._get_recent_reconnect_count()
        if recent_count >= self.INSTABILITY_THRESHOLD:
            return False

        return True

    def _track_comm_timeout(self):
        """Record a timeout event for communication health supervision."""
        now = self.reactor.monotonic()
        self._comm_timeout_timestamps.append(now)
        # Prune old timestamps outside window
        cutoff = now - self.COMM_SUPERVISION_WINDOW
        self._comm_timeout_timestamps = [t for t in self._comm_timeout_timestamps if t > cutoff]

    def _track_comm_unsolicited(self):
        """Record an unsolicited message event for communication health supervision."""
        now = self.reactor.monotonic()
        self._comm_unsolicited_timestamps.append(now)
        # Prune old timestamps outside window
        cutoff = now - self.COMM_SUPERVISION_WINDOW
        self._comm_unsolicited_timestamps = [t for t in self._comm_unsolicited_timestamps if t > cutoff]

    def _check_communication_health(self):
        """
        Check if communication is healthy based on recent timeouts and unsolicited messages.

        Returns:
            tuple: (is_healthy, reason) where is_healthy is bool and reason is string
        """
        now = self.reactor.monotonic()

        # Prune old events
        cutoff = now - self.COMM_SUPERVISION_WINDOW
        self._comm_timeout_timestamps = [t for t in self._comm_timeout_timestamps if t > cutoff]
        self._comm_unsolicited_timestamps = [t for t in self._comm_unsolicited_timestamps if t > cutoff]

        timeout_count = len(self._comm_timeout_timestamps)
        unsolicited_count = len(self._comm_unsolicited_timestamps)

        # Check thresholds - BOTH conditions must be met
        if timeout_count >= self.COMM_TIMEOUT_THRESHOLD and unsolicited_count >= self.COMM_UNSOLICITED_THRESHOLD:
            return False, f"{timeout_count} timeouts AND {unsolicited_count} unsolicited messages in last {self.COMM_SUPERVISION_WINDOW}s"

        return True, "healthy"

    def _supervision_check_and_recover(self):
        """
        Periodically check communication health and force reconnection if unhealthy.
        Called from writer timer.
        """
        # Skip if supervision is disabled
        if not self._supervision_enabled:
            return

        now = self.reactor.monotonic()

        # Only check at intervals to avoid too frequent checks
        if now - self._last_supervision_check < self.SUPERVISION_CHECK_INTERVAL:
            return

        self._last_supervision_check = now

        # Only supervise if connected
        if not self.is_connected():
            return

        is_healthy, reason = self._check_communication_health()

        if not is_healthy:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Communication unhealthy ({reason}), forcing reconnection"
            )
            # Clear the tracking counters before reconnecting
            self._comm_timeout_timestamps = []
            self._comm_unsolicited_timestamps = []
            # Force disconnect and let auto-reconnect handle it
            self.disconnect()

    def get_connection_status(self):
        """
        Get detailed connection status for monitoring.

        Returns:
            dict: Connection status with keys:
                - connected: bool - currently connected
                - stable: bool - stable per rate-based detection
                - recent_reconnects: int - reconnects in last 60s
                - time_connected: float - seconds since last connect
                - last_connected_time: float (monotonic)
        """
        # If we are disconnected and somehow have no reconnect timer (e.g. after
        # an exception path), make sure a timer is scheduled so we don't get
        # stuck showing a static "next retry" message.
        self.ensure_connect_timer()

        now = self.reactor.monotonic()
        recent_count = self._get_recent_reconnect_count()

        time_connected = 0.0
        if self._last_connected_time > 0:
            time_connected = now - self._last_connected_time

        # Get supervision health statistics
        now = self.reactor.monotonic()
        cutoff = now - self.COMM_SUPERVISION_WINDOW
        self._comm_timeout_timestamps = [t for t in self._comm_timeout_timestamps if t > cutoff]
        self._comm_unsolicited_timestamps = [t for t in self._comm_unsolicited_timestamps if t > cutoff]

        timeout_count = len(self._comm_timeout_timestamps)
        unsolicited_count = len(self._comm_unsolicited_timestamps)
        time_since_check = now - self._last_supervision_check

        return {
            "connected": self.is_connected(),
            "stable": self.is_connection_stable(),
            "recent_reconnects": recent_count,
            "time_connected": time_connected,
            "last_connected_time": self._last_connected_time,
            "next_retry": self._reconnect_backoff if not self.is_connected() else 0.0,
            "port": self._port or "unknown",
            "usb_topology": self._usb_location or "unknown",
            "supervision": {
                "timeout_count": timeout_count,
                "timeout_threshold": self.COMM_TIMEOUT_THRESHOLD,
                "unsolicited_count": unsolicited_count,
                "unsolicited_threshold": self.COMM_UNSOLICITED_THRESHOLD,
                "window_seconds": self.COMM_SUPERVISION_WINDOW,
                "check_interval": self.SUPERVISION_CHECK_INTERVAL,
                "time_since_check": time_since_check,
            }
        }

    # ========== CRC Calculation ==========

    def _calc_crc(self, buffer):
        """Calculate CRC-16 for payload."""
        _crc = 0xffff
        for byte in buffer:
            data = byte
            data ^= _crc & 0xff
            data ^= (data & 0x0f) << 4
            _crc = ((data << 8) | (_crc >> 8)) ^ (data >> 4) ^ (data << 3)
        return _crc

    # ========== Request/Response Queuing ==========

    def send_request(self, request, callback):
        """
        Queue a normal-priority request.

        Args:
            request: Dict with JSON-serializable request
            callback: Callable(response=dict) or Callable(response=None) on timeout
        """
        try:
            self._queue.put([request, callback], timeout=1)
        except queue.Full:
            self.gcode.respond_info(f"ACE[{self.instance_num}]: Request queue full!")

    def send_high_prio_request(self, request, callback):
        """
        Queue a high-priority request (processed before normal queue).

        Args:
            request: Dict with JSON-serializable request
            callback: Callable as in send_request
        """
        try:
            self._hp_queue.put([request, callback], timeout=1)
        except queue.Full:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: High-priority queue full!"
            )

    def clear_queues(self):
        """Clear all pending requests."""
        self._clear_queue(self._queue)
        self._clear_queue(self._hp_queue)
        with self._lock:
            self._callback_map.clear()
            self.inflight.clear()

    def _clear_queue(self, q):
        """Remove all items from queue."""
        if q is None:
            return
        try:
            while True:
                q.get_nowait()
        except queue.Empty:
            pass

    # ========== Low-Level Frame Sending ==========

    def _send_frame(self, request):
        """Send a serialized request frame."""
        if not self.is_connected():
            self.gcode.respond_info(f"ACE[{self.instance_num}]: Serial not connected, skipping send")
            return

        with self._lock:
            if 'id' not in request:
                request['id'] = self._request_id
                self._request_id += 1

        payload = json.dumps(request).encode('utf-8')
        data = bytearray([0xFF, 0xAA])
        data += struct.pack('<H', len(payload))
        data += payload
        data += struct.pack('<H', self._calc_crc(payload))
        data += b'\xFE'

        try:
            with self._serial_lock:
                self._serial.write(data)
        except serial.SerialTimeoutException as e:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Serial write timeout: {e} (clearing inflight)"
            )
            with self._lock:
                rid = request.get('id')
                if rid in self.inflight:
                    self.inflight.pop(rid, None)
                    cb = self._callback_map.pop(rid, None)
                    if cb:
                        try:
                            cb(response=None)
                        except Exception as cb_e:
                            self.gcode.respond_info(
                                f"ACE[{self.instance_num}]: Timeout callback error: {cb_e}"
                            )
        except Exception as e:
            self.gcode.respond_info(f"ACE[{self.instance_num}]: Serial write error: {e}")
            with self._lock:
                rid = request.get('id')
                if rid in self.inflight:
                    self.inflight.pop(rid, None)
                    cb = self._callback_map.pop(rid, None)
                    if cb:
                        try:
                            cb(response=None)
                        except Exception as cb_e:
                            self.gcode.respond_info(
                                f"ACE[{self.instance_num}]: Error callback error: {cb_e}"
                            )

    # ========== Frame Reading and Parsing ==========

    # ========== Processing Loop Integration ==========

    def has_pending_requests(self):
        """Check if any requests are queued or in-flight."""
        with self._lock:
            return len(self.inflight) > 0 or not self._queue.empty() or not self._hp_queue.empty()

    def get_pending_request(self):
        """
        Get next request to send (respecting priority).

        Returns:
            tuple: (request_dict, callback) or (None, None) if no requests
        """
        if not self._hp_queue.empty():
            try:
                return self._hp_queue.get_nowait()
            except queue.Empty:
                pass

        if not self._queue.empty():
            try:
                return self._queue.get_nowait()
            except queue.Empty:
                pass

        return None, None

    def dispatch_response(self, response):
        """
        Dispatch response to callback if present, else treat as unsolicited.

        Args:
            response: Response dict

        Returns:
            tuple: (callback, was_solicited) or (None, False) if unsolicited
        """
        rid = response.get('id')
        cb = None

        with self._lock:
            if rid is not None:
                cb = self._callback_map.pop(rid, None)
                if cb:
                    self.inflight.pop(rid, None)

        return cb, cb is not None

    def set_heartbeat_callback(self, callback):
        """
        Set the callback for heartbeat responses.

        Args:
            callback: Function(response) to handle status updates
        """
        self.heartbeat_callback = callback

    def set_on_connect_callback(self, callback):
        """
        Set the callback for successful ACE connection/reconnection.

        Args:
            callback: Function() called after ACE connects
        """
        self.on_connect_callback = callback

    def start_heartbeat(self):
        """
        Start the heartbeat timer to send periodic status requests.

        First request sent immediately, then repeated at heartbeat_interval.
        """
        if self.heartbeat_timer is None:
            # Send first status request immediately
            self._send_heartbeat_request()
            # Register timer for periodic requests
            self.heartbeat_timer = self.reactor.register_timer(
                self._heartbeat_tick,
                self.reactor.NOW
            )
            logging.info(
                f"ACE[{self.instance_num}]: Heartbeat started "
                f"(interval={self.heartbeat_interval}s)"
            )

    def stop_heartbeat(self):
        """Stop the heartbeat timer."""
        if self.heartbeat_timer is not None:
            try:
                self.reactor.unregister_timer(self.heartbeat_timer)
            except Exception as e:
                logging.warning(
                    f"ACE[{self.instance_num}]: Error stopping heartbeat: {e}"
                )
            self.heartbeat_timer = None
            logging.info(
                f"ACE[{self.instance_num}]: Heartbeat stopped"
            )

    def _heartbeat_tick(self, eventtime):
        """Timer callback for periodic heartbeat requests."""
        try:
            now = self.reactor.monotonic()
            self._send_heartbeat_request()
            self._last_status_request_time = now

            return eventtime + self.heartbeat_interval
        except Exception as e:
            logging.warning(
                f"ACE[{self.instance_num}]: Heartbeat tick error: {e}"
            )
            return eventtime + self.heartbeat_interval

    def _send_heartbeat_request(self):
        """Send a status request to the ACE device via the queue."""
        request = {"method": "get_status"}

        def _heartbeat_response(response):
            if self.heartbeat_callback:
                try:
                    self.heartbeat_callback(response)
                except Exception as e:
                    logging.warning(
                        f"ACE[{self.instance_num}]: Heartbeat callback error: {e}"
                    )

        self.send_high_prio_request(request, _heartbeat_response)

    def _writer(self, eventtime):
        """Timer callback: send requests from queue, handle timeouts, fill window."""
        try:
            now = self.reactor.monotonic()

            with self._lock:
                for rid, t0 in list(self.inflight.items()):
                    elapsed = now - t0
                    if elapsed > self.timeout_s:
                        self.gcode.respond_info(
                            f"ACE[{self.instance_num}]: Request ID={rid} TIMEOUT after {elapsed:.1f}s"
                        )
                        # Track timeout for communication health supervision
                        self._track_comm_timeout()
                        cb = self._callback_map.pop(rid, None)
                        if cb:
                            try:
                                cb(response=None)
                            except Exception as e:
                                self.gcode.respond_info(
                                    f"ACE[{self.instance_num}]: Callback error: {e}"
                                )
                        self.inflight.pop(rid, None)

            # Fill window with new requests
            while True:
                with self._lock:
                    if len(self.inflight) >= self.WINDOW_SIZE:
                        break

                req, cb = self.get_pending_request()
                if req is None:
                    # No pending requests - writer loop idle
                    # Heartbeat timer handles periodic status updates
                    break

                with self._lock:
                    rid = self._request_id
                    self._request_id += 1
                    req['id'] = rid
                    self._callback_map[rid] = cb
                    self.inflight[rid] = now

                self._send_frame(req)
        except Exception as e:
            logging.info(f'ACE[{self.instance_num}]: Write error {str(e)}')
            self.gcode.respond_info(str(e))

        # Check communication health and force reconnection if needed
        try:
            self._supervision_check_and_recover()
        except Exception as e:
            logging.warning(f"ACE[{self.instance_num}]: Supervision check error: {e}")

        return eventtime + 0.1

    def _reader(self, eventtime):
        """Timer callback: read frames from serial, dispatch responses."""
        try:
            raw = self._serial.read(size=4096)
        except SerialException:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: Unable to communicate with ACE\n" +
                traceback.format_exc()
            )

            if not self._ace_pro_enabled:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: ACE Pro disabled - not scheduling reconnect"
                )
                return self.reactor.NEVER  # Stop this timer too

            # Try to reconnect
            if self.connect_timer is None:
                self.gcode.respond_info(f"ACE[{self.instance_num}]: Scheduling reconnect")
                self.reconnect()
                return self.reactor.NOW + 1.5
            else:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: Scheduling reconnect (already scheduled)"
                )
            return self.reactor.NEVER

        if raw:
            self.read_buffer += raw
        else:
            return eventtime + 0.05

        while True:
            buf = self.read_buffer
            if len(buf) < 7:
                break

            if not (buf[0] == 0xFF and buf[1] == 0xAA):
                hdr = buf.find(bytes([0xFF, 0xAA]))
                if hdr == -1:
                    self.gcode.respond_info(
                        f"ACE[{self.instance_num}]: Resync: dropped junk ({len(buf)} bytes)"
                    )
                    self.read_buffer = bytearray()
                    break
                else:
                    self.gcode.respond_info(f"ACE[{self.instance_num}]: Resync: skipping {hdr} bytes")
                    self.read_buffer = buf[hdr:]
                    buf = self.read_buffer
                    if len(buf) < 7:
                        break

            payload_len = struct.unpack('<H', buf[2:4])[0]
            frame_len = 2 + 2 + payload_len + 2 + 1

            if len(buf) < frame_len:
                break

            terminator_idx = 4 + payload_len + 2
            if buf[terminator_idx] != 0xFE:
                next_hdr = buf.find(bytes([0xFF, 0xAA]), 1)
                if next_hdr == -1:
                    self.read_buffer = bytearray()
                else:
                    self.read_buffer = buf[next_hdr:]
                self.gcode.respond_info(f"ACE[{self.instance_num}]: Invalid frame tail, resyncing")
                continue

            frame = bytes(buf[:frame_len])
            self.read_buffer = bytearray(buf[frame_len:])

            payload = frame[4:4 + payload_len]
            crc_rx = frame[4 + payload_len:4 + payload_len + 2]
            crc_calc = struct.pack('<H', self._calc_crc(payload))

            if crc_rx != crc_calc:
                self.gcode.respond_info(f"ACE[{self.instance_num}]: Invalid CRC")
                continue

            try:
                ret = json.loads(payload.decode('utf-8'))
            except Exception as e:
                self.gcode.respond_info(f"ACE[{self.instance_num}]: JSON decode error: {e}")
                continue

            if self._status_debug_logging:
                self._status_update_callback(ret)

            cb, was_solicited = self.dispatch_response(ret)
            if cb:
                try:
                    cb(response=ret)
                except Exception as e:
                    self.gcode.respond_info(f"ACE[{self.instance_num}]: Callback error: {e}")
            else:
                # Log unsolicited messages (no matching callback found)
                response_id = ret.get('id', 'no-id')
                response_str = json.dumps(ret)
                self.gcode.respond_info(f"ACE[{self.instance_num}]: UNSOLICITED (ID={response_id}, current_id={self._request_id}): {response_str}")
                # Track unsolicited message for communication health supervision
                self._track_comm_unsolicited()

        return eventtime + 0.05

    def _status_update_callback(self, response):
        """
        Handle status updates with detailed change detection.

        Tracks changes in:
        - Overall status (busy/ready)
        - Action (feeding/retracting/etc)
        - Individual slot status
        - Dryer status
        - Temperature changes
        """
        if not response or "result" not in response:
            return

        result = response.get("result")
        if not result:
            return

        # Extract current state
        current_status = result.get("status")
        current_action = result.get("action", "none")
        current_temp = result.get("temp", 0)
        dryer_status = result.get("dryer_status", {})
        feed_assist_count = result.get("feed_assist_count")
        cont_assist_time = result.get("cont_assist_time")
        slots = result.get("slots", [])

        if current_status is None:
            return

        # Detect overall status/action change
        status_changed = (current_status != self.last_status or
                          current_action != self.last_action)

        if status_changed:
            last_display = f"{self.last_status}/{self.last_action}" if self.last_status else 'unknown'
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: STATUS CHANGE: "
                f"'{last_display}' -> '{current_status}/{current_action}'"
            )
            self.last_status = current_status
            self.last_action = current_action

        # Detect feed assist counters
        if feed_assist_count is not None and feed_assist_count != self.last_feed_assist_count:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: FEED ASSIST COUNT: "
                f"'{self.last_feed_assist_count}' -> '{feed_assist_count}'"
            )
            self.last_feed_assist_count = feed_assist_count

        if cont_assist_time is not None and cont_assist_time != self.last_cont_assist_time:
            self.gcode.respond_info(
                f"ACE[{self.instance_num}]: CONT ASSIST TIME: "
                f"'{self.last_cont_assist_time}' -> '{cont_assist_time}'"
            )
            self.last_cont_assist_time = cont_assist_time

        # Detect slot status changes
        for slot in slots:
            slot_idx = slot.get("index")
            slot_status = slot.get("status", "unknown")

            if slot_idx is not None:
                last_slot_status = self.last_slot_states.get(slot_idx)

                if slot_status != last_slot_status:
                    last_display = last_slot_status if last_slot_status else 'unknown'
                    self.gcode.respond_info(
                        f"ACE[{self.instance_num}]: SLOT[{slot_idx}] CHANGE: "
                        f"'{last_display}' -> '{slot_status}'"
                    )
                    self.last_slot_states[slot_idx] = slot_status

            # Detect any slot field change and dump full slot payload
            if slot_idx is not None:
                last_payload = self.last_slot_payloads.get(slot_idx)
                if last_payload != slot:
                    slot_dump = json.dumps(slot, sort_keys=True)
                    self.gcode.respond_info(
                        f"ACE[{self.instance_num}]: SLOT[{slot_idx}] DATA: {slot_dump}"
                    )
                    self.last_slot_payloads[slot_idx] = slot

        # Detect dryer status changes
        dryer_state = dryer_status.get("status", "stop")
        if dryer_state != self.last_dryer_status:
            if dryer_state != "stop":
                target_temp = dryer_status.get("target_temp", 0)
                remain_time = dryer_status.get("remain_time", 0)
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: DRYER: "
                    f"'{self.last_dryer_status or 'stop'}' -> '{dryer_state}' "
                    f"(target={target_temp}°C, remaining={remain_time}s)"
                )
            else:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: DRYER: stopped"
                )
            self.last_dryer_status = dryer_state

        # Detect significant temperature changes (>5°C)
        if self.last_temp is not None:
            temp_delta = abs(current_temp - self.last_temp)
            if temp_delta >= 5:
                self.gcode.respond_info(
                    f"ACE[{self.instance_num}]: TEMP CHANGE: "
                    f"{self.last_temp}°C -> {current_temp}°C "
                    f"(Δ{temp_delta:+.1f}°C)"
                )
        self.last_temp = current_temp
