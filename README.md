# ovos-phal-plugin-browser

An [OpenVoiceOS](https://openvoiceos.org) PHAL plugin whose hardware is a
**browser tab**: volume, location, power, connectivity, sensors and the
camera, answered on the bus exactly as the device plugins answer them, with
the actual work done by the web page OVOS runs in.

It was made for [Emkay](https://github.com/emkay-voice/emkay), OpenVoiceOS in
the browser (Pyodide), and is what Emkay uses. Any host that runs OVOS in a
page can use it the same way.

## What it answers

| Bus messages | Behaves like | The page does |
|---|---|---|
| `mycroft.volume.get/set/increase/decrease/mute/unmute/mute.toggle` | ovos-PHAL-plugin-alsa: `percent` 0..1, `{percent, muted}` answers and change reports, level kept in the plugin's settings | sets the gain of the one audio node everything plays through |
| `ovos.ipgeo.update` | ovos-phal-plugin-ipgeo: fills the *assistant* config layer (below the user layer, so a location the user set always wins) | asks the browser's geolocation (the user is asked first); OVOS' `get_reverse_geolocation` turns it into city, country and time zone. Until then, the browser's time zone |
| `system.reboot`, `system.shutdown`, `system.mycroft.service.restart`, `system.factory.reset`, `system.configure.language`, `system.ssh.*` | ovos-PHAL-plugin-system | reloads the page, blanks the screen until tapped, wipes the saved data; ssh is reported as not available |
| `ovos.PHAL.internet_check` | ovos-PHAL-plugin-connectivity-events | reports `navigator.onLine` and its changes |
| `ovos.phal.camera.ping/open/close/get` | ovos-PHAL-plugin-camera: `b64_frame` JPEG answers | opens the browser camera (the user is asked first) and takes a picture |
| `ovos.kiosk.fullscreen/keep_awake/install/status` | ovos-skill-kiosk's contract: answered on `<type>.response` (and `<type>.done` after a tap) | full screen, keeps the screen on (wake lock), offers "install as an app"; the first and last need a tap, so it shows a button and answers `needs_tap` |
| `ovos.local_only`, `ovos.local_only.status` | ovos-skill-guardian's contract: `{enabled, stt, tts}` on `<type>.response`, `ovos.local_only.changed` when it switches | STT and TTS move into the page (whisper-browser, piper-browser), the network is refused but for the app's own site and model downloads, a shield shows on the screen. Kept in the user mycroft.conf (`local_only`) |
| `ovos.phal.sensor`, `ovos.phal.binary_sensor` (emitted) | ovos-PHAL-sensors | sends what the browser knows: battery, network, screen on and orientation, moving or still, how the phone lies, compass, storage, memory and CPU cores |

## How a host uses it

The plugin talks to the page through one callable, given when it is made:

```python
from ovos_phal_plugin_browser import BrowserPHAL

plugin = BrowserPHAL(bus=bus, config=config, post=send_to_page,
                     reload_speech=reload_stt_and_tts)
```

`post(change: dict)` gets what the page should do: `{"volume": 0.6, "muted":
false}`, or `{"request": "location" | "camera_check" | "camera_open" |
"camera_close" | "picture" | "reload" | "off" | "forget_welcome"}`,
`{"request": "kiosk", "message": {type, data}}` (the page answers on the bus)
or `{"request": "local_only", "enabled": bool}` (show or hide the shield).
`reload_speech()` is called after local only changed the STT/TTS modules. The page
answers through the plugin's methods: `on_position(lat, lon, error)`,
`on_picture(id, jpeg_b64, error)` and `on_page_event(event, data)` for
`"online"`, `"sensor"`, `"camera"` and `"picture"`.

Emkay's page side is [`web/device.js`](https://github.com/emkay-voice/emkay/blob/main/web/device.js),
and the wiring is in [`worker_main.py`](https://github.com/emkay-voice/emkay/blob/main/py/emkay_mobile/worker_main.py).

On an ordinary device, with real hardware, use the device plugins instead.
The kiosk and local only messages are hardware-neutral: a PHAL plugin for
other hardware can answer the same ones, and the skills work there too.

## Config

`PHAL.ovos-phal-plugin-browser` in `mycroft.conf`; nothing needs setting. The
volume is kept in `~/.config/OpenVoiceOS/ovos-phal-plugin-browser.json`.

## License

Apache-2.0
