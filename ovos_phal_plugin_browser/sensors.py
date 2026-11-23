"""The sensors that need the user's permission, each off until turned on.

What a browser tells without asking (battery, network, screen, storage,
memory, CPU, and motion where the browser doesn't ask) is always reported
(device.py). The sensors here ask the user on the device, so they are off
unless turned on in ``PHAL.ovos-phal-plugin-browser.sensors``:

- ``position``: where the device is, followed over time (Geolocation)
- ``presence``: someone at the screen, and whether it is locked (Idle Detection, Chrome)
- ``mic_level``: how loud the room is, in dBFS (the microphone)
- ``motion``: acceleration, rotation and the magnetic field (Generic Sensors;
  on iOS also the permission the moving/still and position sensors need)

Off means off: the page never touches a sensor that is off, so the browser
never asks for it. Turned on, the page opens it, and the browser asks on the
device. A browser only asks after a tap there, so a sensor turned on from
somewhere else (the house's control panel, Home Assistant) waits, with
"Tap to allow" on the screen, until someone taps it.

The page reports each sensor's state (``sensor_state``): on, waiting (for a
tap), denied, absent (this browser has no such sensor) or off. The control
panel's page asks for them with ``ovos.browser-device.status``.
"""
from ovos_config import Configuration

SENSORS = ("position", "presence", "mic_level", "motion")
STATES = ("on", "waiting", "denied", "absent", "off")
BUS = "ovos.browser-device."


class SensorsMixin:
    """Turning the sensors that ask on and off, for BrowserPHAL."""

    def _sensors_handlers(self):
        return {
            BUS + "reload": self.send_sensors,
            BUS + "status": self.handle_sensors_status,
            "configuration.updated": self._on_config_changed,
        }

    def _sensors_start(self):
        self.sensor_states = {name: "off" for name in SENSORS}
        self._sent_sensors = None
        self.send_sensors()

    @staticmethod
    def wanted_sensors() -> dict:
        """{sensor: on?} from mycroft.conf, as it is now (the panel saves there)."""
        section = (Configuration().get("PHAL", {}).get("ovos-phal-plugin-browser") or {})
        raw = section.get("sensors") or {}
        return {name: bool(raw.get(name, False)) for name in SENSORS}

    def send_sensors(self, message=None):
        """Tell the page which sensors to have on."""
        wanted = self.wanted_sensors()
        self._sent_sensors = wanted
        self._post({"request": "sensors", "on": wanted})

    def _on_config_changed(self, message=None):
        if self.wanted_sensors() != self._sent_sensors:
            self.send_sensors()

    def on_sensor_state(self, name: str, state: str):
        if name in SENSORS and state in STATES:
            self.sensor_states[name] = state

    def sensors_status(self) -> dict:
        """For the panel's status rows: "<sensor>_<state>": true, and the
        states by name."""
        out = {f"{name}_{state}": True for name, state in self.sensor_states.items()}
        out["states"] = dict(self.sensor_states)
        out["any_waiting"] = "waiting" in self.sensor_states.values()
        return out

    def handle_sensors_status(self, message):
        self.bus.emit(message.response(self.sensors_status()))
