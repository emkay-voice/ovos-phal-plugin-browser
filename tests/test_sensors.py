from unittest import mock

from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus

from ovos_phal_plugin_browser import sensors
from ovos_phal_plugin_browser.sensors import SENSORS, SensorsMixin


class Plugin(SensorsMixin):
    def __init__(self, conf):
        self.bus = FakeBus()
        self.posted = []
        self._post = self.posted.append
        self.conf = conf
        for topic, handler in self._sensors_handlers().items():
            self.bus.on(topic, handler)
        with self.patched():
            self._sensors_start()

    def patched(self):
        return mock.patch.object(sensors, "Configuration",
                                 lambda: {"PHAL": {"ovos-phal-plugin-browser": self.conf}})


def test_off_until_turned_on():
    p = Plugin({})
    assert p.posted == [{"request": "sensors", "on": {name: False for name in SENSORS}}]


def test_turned_on_in_the_config_reaches_the_page():
    p = Plugin({})
    p.conf = {"sensors": {"position": True}}
    with p.patched():
        p.bus.emit(Message("configuration.updated"))
        p.bus.emit(Message("configuration.updated"))    # unchanged: not sent again
    assert len(p.posted) == 2
    assert p.posted[-1]["on"]["position"] is True and p.posted[-1]["on"]["presence"] is False


def test_status_for_the_panel():
    p = Plugin({"sensors": {"position": True}})
    p.on_sensor_state("position", "waiting")
    p.on_sensor_state("nonsense", "on")                 # ignored
    answers = []
    p.bus.on("ovos.browser-device.status.response", lambda m: answers.append(m.data))
    p.bus.emit(Message("ovos.browser-device.status"))
    st = answers[0]
    assert st["position_waiting"] and st["any_waiting"] and st["presence_off"]
    assert st["states"]["position"] == "waiting"


def test_a_patch_on_the_bus_reaches_the_page():
    p = Plugin({})
    p.conf = {"sensors": {"motion": True}}
    with p.patched():
        p.bus.emit(Message("configuration.patch", {"config": {}}))
    assert p.posted[-1]["on"]["motion"] is True
