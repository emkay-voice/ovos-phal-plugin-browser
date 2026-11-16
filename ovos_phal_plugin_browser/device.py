"""Connectivity, sensors and camera for the browser PHAL plugin.

Each follows the PHAL plugin a device uses for it, with the browser's APIs
as the hardware (web/device.js reads them and reports changes):

Connectivity - ovos-PHAL-plugin-connectivity-events: the same
  mycroft.network/internet.(dis)connected and .state messages on a change,
  and the same answer to `ovos.PHAL.internet_check`. The state comes from
  the browser (navigator.onLine and its online/offline events), since a
  page cannot open the DNS socket that plugin checks with.

Sensors - ovos-PHAL-sensors' bus logger: every reading is an
  `ovos.phal.sensor` (a value) or `ovos.phal.binary_sensor` (on/off) with
  state, sensor_id "<device>_<name>", device_name, name and attributes
  (Home Assistant style: unit_of_measurement, device_class, icon). The
  control panel's Sensors page shows them. What a phone browser offers:
  battery level and charging, network type and speed, screen on/off and
  orientation, whether the phone is moving (motion sensor), ambient light.

Camera - ovos-PHAL-plugin-camera: ovos.phal.camera.ping/open/close/get.
  `get` answers with {"path"} when a path was given (a JPEG written there)
  or {"b64_frame"}. That plugin sends the raw OpenCV pixel buffer; here
  `b64_frame` is a JPEG, which is what the browser's camera gives and what
  a consumer can decode without knowing the frame size. The browser asks
  the user before the first picture.
"""
import base64
import os
import string

from ovos_bus_client.message import Message
from ovos_utils.log import LOG


def _norm(s):
    """ovos-PHAL-sensors' id normalisation (ascii, lower, underscores)."""
    s = str(s).lower().strip().replace(" ", "_").replace("-", "").replace(".", "_")
    return "".join(c for c in s if c in string.ascii_letters + string.digits + "_")


class DeviceMixin:
    """Connectivity, sensor and camera handlers, for BrowserPHAL."""

    def _device_handlers(self):
        return {
            "ovos.PHAL.internet_check": self.handle_internet_check,
            "ovos.phal.camera.ping": self.handle_camera_pong,
            "ovos.phal.camera.open": self.handle_camera_open,
            "ovos.phal.camera.close": self.handle_camera_close,
            "ovos.phal.camera.get": self.handle_take_picture,
        }

    def _device_start(self):
        self.online = None          # unknown until the page reports
        self.sensors = {}           # sensor_id -> last reading sent
        self._pictures = {}         # request id -> the camera.get message
        self._picture_id = 0
        self._camera_ping = None    # the ovos.phal.camera.ping being answered
        if self.config.get("camera", True):
            self._post({"request": "camera_check"})   # "let the system know"

    # --- page events (worker_main.phal_event) ------------------------------
    def on_page_event(self, event: str, data: dict):
        """A change the page saw: "online", "sensor" or "picture"."""
        if event == "online":
            self.set_online(bool(data.get("online")))
        elif event == "sensor":
            self.sensor_reading(**data)
        elif event == "picture":
            self.on_picture(**data)
        elif event == "camera":
            # the page's answer to camera_check; as the plugin: a pong when
            # there is a camera, to the ping that asked (or to all, on load)
            ping, self._camera_ping = self._camera_ping, None
            if data.get("available"):
                self.bus.emit(ping.reply("ovos.phal.camera.pong") if ping
                              else Message("ovos.phal.camera.pong"))

    # --- connectivity (ovos-PHAL-plugin-connectivity-events) ---------------
    def set_online(self, online: bool, message=None):
        """Emit what connectivity-events emits when the state changes.

        A browser only knows "has a connection" or not, so the state is
        FULL or NONE (the plugin's LIMITED, network without internet, does
        not occur).
        """
        if online == self.online:
            return
        was, self.online = self.online, online
        message = message or Message("connectivity_check")
        LOG.info(f"Network state changed to: {'FULL' if online else 'NONE'}")
        if online:
            self.bus.emit(message.reply("mycroft.network.connected"))
            self.bus.emit(message.reply("mycroft.internet.connected"))
        else:
            if was:
                self.bus.emit(message.reply("mycroft.internet.disconnected"))
                self.bus.emit(message.reply("mycroft.network.disconnected"))
            self.bus.emit(message.reply("enclosure.notify.no_internet"))
        state = "connected" if online else "disconnected"
        self.bus.emit(message.reply("mycroft.internet.state", {"state": state}))
        self.bus.emit(message.reply("mycroft.network.state", {"state": state}))

    def handle_internet_check(self, message):
        online = bool(self.online) if self.online is not None else True
        self.bus.emit(message.response({"internet_connected": online,
                                        "network_connected": online}))

    # --- sensors (ovos-PHAL-sensors' MessageBusLogger) -----------------------
    def sensor_reading(self, device, name, state, binary=False, attributes=None):
        """Emit one reading, when it differs from the last one sent."""
        device, name = _norm(device), _norm(name)
        sensor_id = f"{device}_{name}"
        attributes = dict(attributes or {})
        attributes.setdefault("friendly_name", f"{device} {name}".replace("_", " ").title())
        if self.sensors.get(sensor_id) == (state, attributes):
            return
        self.sensors[sensor_id] = (state, attributes)
        topic = "ovos.phal.binary_sensor" if binary else "ovos.phal.sensor"
        self.bus.emit(Message(topic, {"state": state, "sensor_id": sensor_id,
                                      "device_name": device, "name": name,
                                      "attributes": attributes}))

    # --- camera (ovos-PHAL-plugin-camera) -----------------------------------
    def handle_camera_pong(self, message):
        # the page has a camera when the browser offers one (mediaDevices);
        # whether the user allows it is only known at the first picture
        if self.validate_message_context(message) and self.config.get("camera", True):
            self._camera_ping = message
            self._post({"request": "camera_check"})

    def handle_camera_open(self, message):
        if self.validate_message_context(message):
            self._post({"request": "camera_open"})

    def handle_camera_close(self, message):
        if self.validate_message_context(message):
            self._post({"request": "camera_close"})

    def handle_take_picture(self, message):
        """Ask the page for a frame; on_picture answers the request."""
        if not self.validate_message_context(message):
            return
        self._picture_id += 1
        self._pictures[self._picture_id] = message
        self._post({"request": "picture", "id": self._picture_id})

    def on_picture(self, id, jpeg_b64=None, error=None):
        message = self._pictures.pop(int(id), None)
        if message is None:
            return
        if error or not jpeg_b64:
            LOG.error(f"No picture from the browser: {error}")
            self.bus.emit(message.response({"error": str(error)}, False))
            return
        path = message.data.get("path")
        if path:
            try:
                path = os.path.expanduser(path)
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                with open(path, "wb") as f:
                    f.write(base64.b64decode(jpeg_b64))
                LOG.info(f"Picture saved: {path}")
                self.bus.emit(message.response({"path": path}))
            except Exception as e:
                LOG.error(f"Error saving image: {e}")
                self.bus.emit(message.response({"error": str(e)}, False))
        else:
            self.bus.emit(message.response({"b64_frame": jpeg_b64}))
