"""Kiosk: the screen as a dedicated device, for the browser PHAL plugin.

The bus contract is hardware-neutral (ovos-skill-kiosk sends it; another
PHAL plugin can answer it for its own screen, e.g. a Pi with a kiosk
browser):

    ovos.kiosk.fullscreen   {"enabled": bool}
    ovos.kiosk.keep_awake   {"enabled": bool}
    ovos.kiosk.install      {}
    ovos.kiosk.status       {}
    -> <type>.response      {"fullscreen", "keep_awake", "installed", ...,
                             "needs_tap"?: true, "supported"?: false}
    -> <type>.done          the same, once a tap has finished the job

No answer means the device has no screen to manage.

A browser only allows full screen and "install as an app" right after a
tap, so the page shows a button for it and answers `needs_tap`; the page
answers on the bus itself (Emkay's web/app.js onKiosk). This plugin hands
it the request: `{"request": "kiosk", "message": {type, data}}`.
"""

KIOSK_TOPICS = ("ovos.kiosk.fullscreen", "ovos.kiosk.keep_awake",
                "ovos.kiosk.install", "ovos.kiosk.status")


class KioskMixin:
    """`ovos.kiosk.*` handlers, for BrowserPHAL."""

    def _kiosk_handlers(self):
        return {topic: self.handle_kiosk for topic in KIOSK_TOPICS}

    def handle_kiosk(self, message):
        self._post({"request": "kiosk",
                    "message": {"type": message.msg_type, "data": message.data or {}}})
