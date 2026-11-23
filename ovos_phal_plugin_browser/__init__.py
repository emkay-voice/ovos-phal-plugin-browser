"""The phone's "hardware" for OVOS: a PHAL plugin whose device is the browser.

On a device, PHAL plugins answer OVOS' hardware messages: ovos-PHAL-plugin-
alsa turns `mycroft.volume.*` into mixer changes, ipgeo finds the location,
and so on. Here the hardware is the page, so this plugin answers the same
messages, with the same data, and hands the actual change to the page.

Volume follows ovos-PHAL-plugin-alsa exactly: `percent` is 0..1 on the bus,
`mycroft.volume.get` is answered with {"percent", "muted"}, every change is
reported the same way, and the level is kept in the plugin's own settings
file (~/.config/OpenVoiceOS/ovos-phal-plugin-browser.json, which lives in
IndexedDB like the rest of ~/.config). The page plays everything through
one gain node, which is this plugin's "mixer".

Location follows ovos-phal-plugin-ipgeo, which a device uses on first boot:
it fills the *assistant* config layer (below the user layer, so a location
the user sets always wins), only when that layer has none, and again on
`ovos.ipgeo.update` (with "overwrite"). ipgeo asks http://ip-api.com, which
an https page may not call, so the position comes from the browser's own
geolocation instead (it asks the user first) and OVOS' own
`get_reverse_geolocation` turns it into city, country and time zone. Until
then, or if the user says no, the layer gets the browser's time zone, so
clocks and timers are right from the start.

Power, factory reset and language (ovos-PHAL-plugin-system) are in
system.py; connectivity, sensors and the camera in device.py, and the
sensors that ask the user first (off until turned on) in sensors.py; the kiosk
(full screen, screen on, install as an app) in kiosk.py; local only
(nothing leaves the device) in local_only.py. Their bus messages are
hardware-neutral, so another PHAL plugin can answer the same ones on other
hardware and the skills that send them (ovos-skill-kiosk,
ovos-skill-guardian) work there too.
"""
import os
import json

from ovos_bus_client.message import Message
from ovos_bus_client.session import SessionManager
from ovos_bus_client.util import get_message_lang
from ovos_config import Configuration
from ovos_config.models import AssistantConfig
from ovos_plugin_manager.phal import PHALPlugin
from ovos_utils.log import LOG

from .device import DeviceMixin
from .kiosk import KioskMixin
from .local_only import LocalOnlyMixin
from .sensors import SensorsMixin
from .system import SystemMixin

try:
    from json_database import JsonConfigXDG
except ImportError:  # pragma: no cover
    JsonConfigXDG = None


class BrowserPHAL(SystemMixin, DeviceMixin, SensorsMixin, KioskMixin, LocalOnlyMixin, PHALPlugin):
    """PHAL for a browser tab: volume, location, power, connectivity,
    sensors and camera, each with the bus contract of the plugin a device
    uses for it; plus kiosk and local only."""

    def __init__(self, bus=None, config=None, post=None, reload_speech=None):
        """
        Args:
            bus: the OVOS bus
            config: the plugin's PHAL config section (default_volume)
            post: callable(dict) that hands a state change to the page
            reload_speech: callable() that makes the next sentence use a
                changed STT/TTS (local only)
        """
        super().__init__(bus=bus, name="ovos-phal-plugin-browser", config=config or {})
        self._post = post or (lambda change: None)
        self.reload_speech = reload_speech
        self.settings = (JsonConfigXDG(self.name, subfolder="OpenVoiceOS")
                         if JsonConfigXDG else {})
        if self.settings.get("first_boot", True):
            self.settings["volume"] = int(self.config.get("default_volume", 75))
            self.settings["muted"] = False
            self.settings["first_boot"] = False
            self._store()
        self._system_start()
        self._device_start()
        self._sensors_start()
        self._local_only_start()
        for topic, handler in self._handlers().items():
            self.bus.on(topic, handler)
        self._report()  # the page starts at the stored level
        self.assistant_config = AssistantConfig()
        self._location_request = None  # the ovos.ipgeo.update waiting for an answer
        self.on_location_update()       # first boot: as ipgeo does

    def _handlers(self):
        return {
            "mycroft.volume.get": self.handle_volume_request,
            "mycroft.volume.set": self.handle_volume_change,
            "mycroft.volume.increase": self.handle_volume_increase,
            "mycroft.volume.decrease": self.handle_volume_decrease,
            "mycroft.volume.mute": self.handle_mute_request,
            "mycroft.volume.unmute": self.handle_unmute_request,
            "mycroft.volume.mute.toggle": self.handle_mute_toggle_request,
            "ovos.ipgeo.update": self.on_location_update,
            **self._system_handlers(),
            **self._device_handlers(),
            **self._sensors_handlers(),
            **self._kiosk_handlers(),
            **self._local_only_handlers(),
        }

    # --- the "mixer" ----------------------------------------------------
    @property
    def volume(self) -> int:
        """Current level, 0..100."""
        return int(self.settings.get("volume", 75))

    @property
    def muted(self) -> bool:
        return bool(self.settings.get("muted", False))

    def _store(self):
        if hasattr(self.settings, "store"):
            self.settings.store()

    def _report(self):
        """Tell the page the level to play at."""
        self._post({"volume": self.volume / 100, "muted": self.muted})

    def set_volume(self, percent, play_sound=True):
        """Set the level (0..100), clamped, as the alsa plugin does."""
        self.settings["volume"] = max(0, min(100, int(percent)))
        self._store()
        self._report()
        if play_sound:
            self.bus.emit(Message("mycroft.audio.play_sound",
                                  {"uri": "snd/blop-mark-diangelo.wav"}))
        self.handle_volume_request(Message("mycroft.volume.get"))

    def set_muted(self, muted: bool):
        self.settings["muted"] = bool(muted)
        self._store()
        self._report()
        self.handle_volume_request(Message("mycroft.volume.get"))

    # --- location (same contract as ovos-phal-plugin-ipgeo) -------------
    def _has_location(self) -> bool:
        """True once the assistant layer holds a real location (a city), not
        just the time-zone stand-in written below."""
        loc = self.assistant_config.get("location") or {}
        return bool(loc.get("city"))

    def on_location_update(self, message=None):
        """Ask the page for the phone's position, unless a location is known
        and the request does not ask to overwrite it (as ipgeo's on_reset)."""
        if self._has_location() and (message is None or not message.data.get("overwrite")):
            LOG.debug("Skipping overwrite of existing location")
            return
        if not self.assistant_config.get("location"):
            self._store_location({"timezone": self._browser_timezone()})
        self._location_request = message
        self._post({"request": "location"})

    @staticmethod
    def _browser_timezone() -> dict:
        tz = os.environ.get("EMKAY_TZ") or "UTC"  # set by worker.js
        return {"code": tz, "name": tz.replace("/", " ")}

    def on_position(self, lat=None, lon=None, error=None):
        """The page's answer: coordinates, or why there are none."""
        message, self._location_request = self._location_request, None
        if error or lat is None or lon is None:
            LOG.info(f"No position from the browser: {error}")
            if message:
                self.bus.emit(message.response(data={"error": True}))
            return
        try:
            from ovos_utils.geolocation import get_reverse_geolocation
            lang = get_message_lang(message) or Configuration().get("lang", "en")
            location = get_reverse_geolocation(float(lat), float(lon), lang=lang)
        except Exception as e:  # offline, service refused: keep what we have
            LOG.error(f"Reverse geolocation failed: {e}")
            if message:
                self.bus.emit(message.response(data={"error": True}))
            return
        LOG.info(f"Browser geolocation: {location.get('address')}")
        self._store_location(location)
        if message:
            self.bus.emit(message.response(data={"location": location}))

    def _store_location(self, location: dict):
        """Write the assistant layer and tell OVOS, as ipgeo does.

        The device's default session carries its own copy of the location
        (OVOS-SESSION-1 stamps lat/lon/tz into it when it is created), and
        skills read the time zone from there. On a device ipgeo has usually
        run before the first question; here the position may come minutes
        later, when the user allows it, so the default session is created
        anew from the updated configuration.
        """
        self.assistant_config["location"] = location
        self.assistant_config.store()
        self.bus.emit(Message("configuration.updated"))
        SessionManager.reset_default_session()

    # --- bus handlers (same contract as ovos-PHAL-plugin-alsa) -----------
    def validate_message_context(self, message) -> bool:
        """Only the device's own session controls its volume (as alsa)."""
        sid = SessionManager.get(message).session_id
        native = getattr(self.bus, "session_id", "default")
        return sid == native

    def handle_volume_request(self, message):
        if not self.validate_message_context(message):
            return
        self.bus.emit(message.response({"percent": self.volume / 100,
                                        "muted": self.muted}))

    def handle_volume_change(self, message):
        if not self.validate_message_context(message):
            return
        self.set_volume(message.data["percent"] * 100,
                        play_sound=message.data.get("play_sound", True))

    def handle_volume_increase(self, message):
        if not self.validate_message_context(message):
            return
        change = message.data.get("percent", .10) * 100 or 15
        self.set_volume(self.volume + abs(change),
                        play_sound=message.data.get("play_sound", True))

    def handle_volume_decrease(self, message):
        if not self.validate_message_context(message):
            return
        change = message.data.get("percent", -.10) * 100 or 15
        self.set_volume(self.volume - abs(change),
                        play_sound=message.data.get("play_sound", True))

    def handle_mute_request(self, message):
        if self.validate_message_context(message):
            LOG.info("User muted audio.")
            self.set_muted(True)

    def handle_unmute_request(self, message):
        if self.validate_message_context(message):
            LOG.info("User unmuted audio.")
            self.set_muted(False)

    def handle_mute_toggle_request(self, message):
        if self.validate_message_context(message):
            self.set_muted(not self.muted)

    def shutdown(self):
        for topic, handler in self._handlers().items():
            self.bus.remove(topic, handler)
        super().shutdown()


_plugin = None


def start(bus, post, reload_speech=None):
    """Load the plugin on the bus; `post(str)` sends JSON to the page,
    `reload_speech()` applies a changed STT/TTS (local only)."""
    global _plugin
    conf = Configuration().get("PHAL", {}).get("ovos-phal-plugin-browser", {})
    _plugin = BrowserPHAL(bus=bus, config=conf, post=lambda change: post(json.dumps(change)),
                          reload_speech=reload_speech)
    return _plugin


def plugin():
    """The loaded plugin (None before start())."""
    return _plugin
