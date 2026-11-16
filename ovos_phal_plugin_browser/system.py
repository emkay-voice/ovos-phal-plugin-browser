"""Power, factory reset and language for the browser PHAL plugin.

Same bus contract as ovos-PHAL-plugin-system, which a device runs: the same
messages in, the same `.start` / `.complete` messages and GUI animations out.
What differs is the machine: on a device these end in `systemctl`, here
OVOS *is* the page, so

  system.reboot                   -> the page reloads (after saving to disk)
  system.mycroft.service.restart  -> the same: every OVOS service is the page
  system.shutdown                 -> the page goes dark; a tap starts it again
  system.factory.reset            -> the plugin's own wipe (cache, data, logs,
                                     configs), then a reboot, so Emkay starts
                                     as on the first day (and offers setup)
  system.ssh.*                    -> a browser has no SSH: status says off

The page part is in web/device.js.
"""
import os
import shutil
from os.path import expanduser, join

from ovos_bus_client.message import Message
from ovos_utils.log import LOG


class SystemMixin:
    """ovos-PHAL-plugin-system's handlers, for BrowserPHAL."""

    def _system_handlers(self):
        return {
            "system.ssh.status": self.handle_ssh_status,
            "system.ssh.enable": self.handle_ssh_change,
            "system.ssh.disable": self.handle_ssh_change,
            "system.reboot": self.handle_reboot_request,
            "system.reboot.start": self.handle_rebooting,
            "system.shutdown": self.handle_shutdown_request,
            "system.shutdown.start": self.handle_shutting_down,
            "system.factory.reset": self.handle_factory_reset_request,
            "system.factory.reset.register": self.handle_reset_register,
            "system.configure.language": self.handle_configure_language_request,
            "system.mycroft.service.restart": self.handle_mycroft_restart_request,
            "system.mycroft.service.restart.start": self.handle_mycroft_restarting,
        }

    def _system_start(self):
        from ovos_bus_client.apis.gui import GUIInterface
        self.gui = GUIInterface(bus=self.bus, skill_id=self.name)
        self.factory_reset_plugs = []
        # as the system plugin: let PHAL plugins with a reset step register
        self.bus.emit(Message("system.factory.reset.ping"))

    # --- power -----------------------------------------------------------
    def handle_reboot_request(self, message):
        self.bus.emit(message.forward("system.reboot.start", message.data))
        LOG.info("Reboot requested: reloading the page")
        self._post({"request": "reload"})

    def handle_rebooting(self, message):
        if message.data.get("display", True):
            self.gui.show_loading_animation("Rebooting", override_animations=True,
                                            override_idle=True)

    def handle_mycroft_restart_request(self, message):
        self.bus.emit(message.forward("system.mycroft.service.restart.start", message.data))
        LOG.info("Service restart requested: reloading the page")
        self._post({"request": "reload"})

    def handle_mycroft_restarting(self, message):
        if message.data.get("display", True):
            self.gui.show_loading_animation("Restarting", override_animations=True,
                                            override_idle=True)

    def handle_shutdown_request(self, message):
        self.bus.emit(message.forward("system.shutdown.start", message.data))
        LOG.info("Shutdown requested: the page goes dark")
        self._post({"request": "off"})

    def handle_shutting_down(self, message):
        if message.data.get("display", True):
            self.gui.show_loading_animation("Shutting Down", override_animations=True,
                                            override_idle=True)

    # --- factory reset (ovos-PHAL-plugin-system's steps) ------------------
    def handle_reset_register(self, message):
        sid = message.data.get("skill_id")
        if sid and sid not in self.factory_reset_plugs:
            self.factory_reset_plugs.append(sid)

    def handle_factory_reset_request(self, message):
        from json_database import JsonDatabaseXDG, JsonStorageXDG
        from ovos_config.locations import ASSISTANT_CONFIG, USER_CONFIG, WEB_CONFIG_CACHE
        from ovos_config.meta import get_config_filename, get_xdg_base
        from ovos_utils.xdg_utils import xdg_cache_home, xdg_data_home, xdg_state_home

        LOG.debug(f"Factory reset request: {message.data}")
        self.bus.emit(message.forward("system.factory.reset.start"))
        self.bus.emit(message.forward("system.factory.reset.ping"))
        base = get_xdg_base()

        if message.data.get("wipe_cache", True):
            shutil.rmtree(f"{xdg_cache_home()}/{base}", ignore_errors=True)
        if message.data.get("wipe_data", True):
            shutil.rmtree(f"{xdg_data_home()}/{base}", ignore_errors=True)
            for j in ["ovos_device_info", "ovos_oauth", "ovos_oauth_apps", "ovos_devices",
                      "ovos_metrics", "ovos_preferences", "ovos_skills_meta"]:
                p = JsonStorageXDG(j).path
                if os.path.isfile(p):
                    os.remove(p)
            for j in ["ovos_metrics", "ovos_utterances", "ovos_wakewords"]:
                p = JsonDatabaseXDG(j).db.path
                if os.path.isfile(p):
                    os.remove(p)
            # the pre-trained intents lived in the data folder just wiped;
            # forget that they were downloaded, so the next boot fetches them
            # again instead of training on the phone (worker.js)
            stamp = expanduser("~/.local/share/emkay-mobile/downloads.json")
            if os.path.isfile(stamp):
                os.remove(stamp)
        if message.data.get("wipe_logs", True):
            shutil.rmtree(f"{xdg_state_home()}/{base}", ignore_errors=True)
        if message.data.get("wipe_configs", True):
            old = join(expanduser("~"), "." + base, get_config_filename())
            for p in (old, USER_CONFIG, ASSISTANT_CONFIG, WEB_CONFIG_CACHE):
                if os.path.isfile(p):
                    os.remove(p)
        LOG.debug("Data reset completed")

        if message.data.get("reset_hardware", True) and self.factory_reset_plugs:
            # plugins answer on this same bus while the message is emitted
            done = set()
            on_done = lambda m: done.add(m.data.get("skill_id"))
            self.bus.on("system.factory.reset.phal.complete", on_done)
            self.bus.emit(message.forward("system.factory.reset.phal", message.data))
            self.bus.remove("system.factory.reset.phal.complete", on_done)
            missing = set(self.factory_reset_plugs) - done
            if missing:
                LOG.warning(f"No reset answer from: {sorted(missing)}")

        # no reset script here (there is no shell); the page also forgets
        # that Emkay was started before, so it offers setup again
        self.bus.emit(message.forward("system.factory.reset.complete"))
        self._post({"request": "forget_welcome"})
        if message.data.get("reboot", True):
            self.bus.emit(message.forward("system.reboot"))

    # --- language, ssh -----------------------------------------------------
    def handle_configure_language_request(self, message):
        from ovos_config.config import update_mycroft_config
        from ovos_config.locale import set_default_lang
        language_code = message.data.get("language_code", "en_US")
        language_code = language_code.lower().replace("_", "-")   # no ~/.bash_profile here
        set_default_lang(language_code)
        update_mycroft_config({"lang": language_code}, bus=self.bus)
        if message.data.get("display", False):
            self.gui.show_status_animation(f"Language changed to {language_code}", True)
        self.bus.emit(Message("system.configure.language.complete", {"lang": language_code}))

    def handle_ssh_status(self, message):
        self.bus.emit(message.response(data={"enabled": False}))

    def handle_ssh_change(self, message):
        LOG.warning(f"{message.msg_type}: a browser has no SSH server")
