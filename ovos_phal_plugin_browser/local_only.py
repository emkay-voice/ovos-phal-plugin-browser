"""Local only: nothing leaves the device, for the browser PHAL plugin.

The bus contract is hardware-neutral, so a skill can switch it on any
device whose PHAL answers it (ovos-skill-guardian: "go offline", "go
online", "are you online?"):

    ovos.local_only          {"enabled": bool}
    ovos.local_only.status   {}
    -> <type>.response       {"enabled": bool, "stt": module, "tts": module}
    ovos.local_only.changed  {"enabled": bool, "stt": module, "tts": module}

No answer means the device can't. Another PHAL plugin can implement the
same messages for its own hardware, e.g. on a Raspberry Pi: switch to a
local STT and TTS and close the firewall.

Here, in a browser tab, local only means:

- speech recognition and the voice run in the page
  (ovos-stt-plugin-whisper-browser, ovos-tts-plugin-piper-browser)
- the worker's network layer refuses every request except the app's own
  site and GET downloads of the models and voices those two need
  (`js.emkayNet.setLocalOnly`, Emkay's web/netlog.js)
- the page shows a small shield (`{"request": "local_only"}` to the page)

The state, and the online plugins to go back to, are kept in the user
mycroft.conf (`local_only`), so it lasts across restarts.
"""
from ovos_bus_client.message import Message
from ovos_config import Configuration
from ovos_utils.log import LOG

LOCAL_STT = "ovos-stt-plugin-whisper-browser"
LOCAL_TTS = "ovos-tts-plugin-piper-browser"
ONLINE_STT = "ovos-stt-plugin-server"
ONLINE_TTS = "ovos-tts-plugin-server"
KEY = "local_only"


def _user_conf():
    from ovos_config.config import LocalConf
    from ovos_config.locations import USER_CONFIG
    return LocalConf(USER_CONFIG)


class LocalOnlyMixin:
    """`ovos.local_only` handlers, for BrowserPHAL."""

    #: called after STT/TTS changed, so the next sentence uses the new
    #: plugins (Emkay passes its speech.reload); set by start()
    reload_speech = None

    def _local_only_handlers(self):
        return {
            "ovos.local_only": self.handle_local_only,
            "ovos.local_only.status": self.handle_local_only_status,
        }

    def _local_only_start(self):
        if self.local_only_enabled():   # saved "on": apply it at boot
            self._local_only_network(True)
            self._post({"request": "local_only", "enabled": True})

    # --- state -------------------------------------------------------------

    def local_only_enabled(self) -> bool:
        return bool((Configuration().get(KEY) or {}).get("enabled"))

    def local_only_status(self) -> dict:
        cfg = Configuration()
        return {"enabled": self.local_only_enabled(),
                "stt": cfg.get("stt", {}).get("module"),
                "tts": cfg.get("tts", {}).get("module")}

    def set_local_only(self, on: bool) -> dict:
        """Switch; returns the status. Switching to what it is already is fine."""
        state = Configuration().get(KEY) or {}
        patch = None
        if on and not state.get("enabled"):
            cfg = Configuration()
            online = {"stt": cfg.get("stt", {}).get("module"),
                      "tts": cfg.get("tts", {}).get("module")}
            # nothing to fall back on while a model downloads: wait for it
            patch = {KEY: {"enabled": True, "online": online},
                     "stt": {"module": LOCAL_STT, LOCAL_STT: {"wait_for_download": True}},
                     "tts": {"module": LOCAL_TTS, LOCAL_TTS: {"wait_for_download": True}}}
        elif not on and state.get("enabled"):
            online = state.get("online") or {}
            patch = {KEY: {"enabled": False, "online": {}},
                     "stt": {"module": online.get("stt") or ONLINE_STT,
                             LOCAL_STT: {"wait_for_download": False}},
                     "tts": {"module": online.get("tts") or ONLINE_TTS,
                             LOCAL_TTS: {"wait_for_download": False}}}
        if patch:
            conf = _user_conf()
            conf.merge(patch)
            conf.store()
            Configuration.reload()
            if self.reload_speech:
                self.reload_speech()
        self._local_only_network(on)
        self._post({"request": "local_only", "enabled": bool(on)})
        status = self.local_only_status()
        if patch:
            self.bus.emit(Message("ovos.local_only.changed", status))
        return status

    @staticmethod
    def _local_only_network(on: bool):
        """Tell the worker's network layer (Emkay's web/netlog.js)."""
        try:
            import js
            js.emkayNet.setLocalOnly(bool(on))
        except Exception as e:          # not in a browser (tests)
            LOG.debug(f"local only: network layer not reached ({e!r})")

    # --- bus ---------------------------------------------------------------

    def handle_local_only(self, message):
        on = bool((message.data or {}).get("enabled", True))
        self.bus.emit(message.response(self.set_local_only(on)))

    def handle_local_only_status(self, message):
        self.bus.emit(message.response(self.local_only_status()))
