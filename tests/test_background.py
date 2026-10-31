import contextlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ossi import autostart, instance, notifications, router, storage


class InstanceTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        patcher = mock.patch.dict(os.environ, {"OSSI_HOME": folder.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_nothing_running(self):
        self.assertFalse(instance.signal_running())

    def test_a_stale_port_file_means_nothing_is_running(self):
        listener = instance.Listener()
        port = instance.port_file().read_text()
        listener.close()
        instance.port_file().write_text(port)
        self.assertFalse(instance.signal_running())

    def test_a_corrupt_port_file_means_nothing_is_running(self):
        instance.port_file().parent.mkdir(parents=True, exist_ok=True)
        instance.port_file().write_text("not a port")
        self.assertFalse(instance.signal_running())

    def run_signal(self, listener, show):
        """The signal blocks on a reply, so answer it from another thread."""
        import threading

        answers = {}
        thread = threading.Thread(
            target=lambda: answers.update(running=instance.signal_running(show=show))
        )
        thread.start()
        asked_to_show = False
        while thread.is_alive():
            asked_to_show = listener.poll() or asked_to_show
            thread.join(0.01)
        return answers["running"], asked_to_show

    def test_a_new_launch_finds_the_running_copy_and_asks_to_show(self):
        listener = instance.Listener()
        self.addCleanup(listener.close)
        self.assertEqual(self.run_signal(listener, show=True), (True, True))

    def test_a_background_launch_does_not_pop_the_window_open(self):
        listener = instance.Listener()
        self.addCleanup(listener.close)
        self.assertEqual(self.run_signal(listener, show=False), (True, False))

    def test_the_same_code_just_reopens_the_window(self):
        listener = instance.Listener()
        self.addCleanup(listener.close)
        self.assertEqual(self.run_signal(listener, show=True), (True, True))
        self.assertFalse(listener.replaced)

    def test_older_code_does_not_replace_a_newer_copy(self):
        listener = instance.Listener()
        self.addCleanup(listener.close)
        with mock.patch.object(instance, "THIS_CODE", instance.THIS_CODE - 100):
            running, _ = self.run_signal(listener, show=True)
        self.assertTrue(running)
        self.assertFalse(listener.replaced)

    def launch_newer_code(self, listener, close_after=None):
        """Launch with newer code, with the running copy closing as the window would."""
        import threading

        answers = {}

        def launch():
            answers["running"] = instance.signal_running()

        # The listener compares against the same constant, so only the request is changed.
        real_create = instance.socket.create_connection

        def newer(*args, **kwargs):
            connection = real_create(*args, **kwargs)
            real_send = connection.sendall

            class Connection:
                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    connection.close()

                def sendall(self, data):
                    real_send(data.split()[0] + b" %d" % (instance.THIS_CODE + 100))

                def recv(self, size):
                    return connection.recv(size)

            return Connection()

        with mock.patch.object(instance.socket, "create_connection", newer):
            thread = threading.Thread(target=launch)
            thread.start()
            while thread.is_alive():
                listener.poll()
                if listener.replaced and close_after is not None:
                    close_after()
                thread.join(0.01)
        return answers["running"]

    def test_newer_code_replaces_the_running_copy(self):
        listener = instance.Listener()
        closed = []

        def close_once():
            if not closed:
                closed.append(True)
                listener.close()

        self.assertFalse(self.launch_newer_code(listener, close_once))
        self.assertTrue(listener.replaced)
        self.assertFalse(instance.port_file().exists())

    def test_an_old_copy_that_will_not_close_keeps_this_launch_from_starting_a_second(self):
        listener = instance.Listener()
        self.addCleanup(listener.close)
        with mock.patch.object(instance, "WAIT_FOR_OLD_COPY_SECONDS", 0.1):
            self.assertTrue(self.launch_newer_code(listener))

    def test_junk_on_the_port_is_ignored(self):
        import socket

        listener = instance.Listener()
        self.addCleanup(listener.close)
        port = int(instance.port_file().read_text())
        for junk in (b"hello", b"ossi-show notanumber", b""):
            with socket.create_connection(("127.0.0.1", port), timeout=1) as connection:
                connection.sendall(junk)
                connection.shutdown(socket.SHUT_WR)
                listener.server.setblocking(True)
                listener.server.settimeout(1)
                accepted, _ = listener.server.accept()
                listener.server.setblocking(False)
                accepted.settimeout(0.5)
                listener.answer(accepted)
                accepted.close()
        self.assertFalse(listener.replaced)

    def test_code_stamp_follows_the_newest_file(self):
        self.assertGreaterEqual(instance.code_stamp(), instance.THIS_CODE)

    def test_closing_removes_the_port_file(self):
        instance.Listener().close()
        self.assertFalse(instance.port_file().exists())


class NotificationTest(unittest.TestCase):
    def test_xml_special_characters_stay_as_text(self):
        message = 'buy <milk> & "eggs" $(Remove-Item x)'
        xml = notifications.toast_xml("Reminder", message)
        texts = [element.text for element in ElementTree.fromstring(xml).iter("text")]
        self.assertEqual(texts, ["Reminder", message])

    def test_control_characters_are_removed(self):
        ElementTree.fromstring(notifications.toast_xml("Reminder", "a\x00b\x1fc"))

    def test_failure_to_start_returns_none(self):
        with mock.patch("subprocess.Popen", side_effect=OSError), mock.patch.object(
            notifications, "register_app"
        ):
            self.assertIsNone(notifications.send("Reminder", "call the bank"))

    def test_message_is_not_placed_in_the_powershell_command(self):
        with mock.patch("subprocess.Popen") as popen, mock.patch.object(
            notifications, "register_app"
        ):
            notifications.send("Reminder", "UNIQUE-MESSAGE")
        args, kwargs = popen.call_args
        self.assertNotIn("UNIQUE-MESSAGE", " ".join(args[0]))
        self.assertIn("UNIQUE-MESSAGE", kwargs["env"]["OSSI_TOAST_XML"])

    def test_icon_exists(self):
        self.assertTrue(notifications.ICON.is_file())


class FakeWinreg:
    """Stands in for the Windows registry, so tests can't change real login settings."""

    HKEY_CURRENT_USER = "HKCU"
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.values = {}

    @contextlib.contextmanager
    def OpenKey(self, *args):
        yield self

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[name] = value

    def DeleteValue(self, key, name):
        if name not in self.values:
            raise FileNotFoundError
        del self.values[name]

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError
        return self.values[name], self.REG_SZ


class AutostartTest(unittest.TestCase):
    def setUp(self):
        self.registry = FakeWinreg()
        for patcher in (
            mock.patch.dict(sys.modules, {"winreg": self.registry}),
            mock.patch.object(autostart, "supported", lambda: True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.db = storage.connect(":memory:")
        self.addCleanup(self.db.close)

    def test_turning_on_and_off(self):
        self.assertFalse(autostart.is_enabled())
        self.assertIn("will start", router.handle(self.db, "start at login"))
        self.assertTrue(autostart.is_enabled())
        self.assertIn("--background", self.registry.values[autostart.VALUE_NAME])
        self.assertIn("won't start", router.handle(self.db, "don't start at login"))
        self.assertFalse(autostart.is_enabled())

    def test_disabling_twice_is_fine(self):
        autostart.disable()
        autostart.disable()

    def test_command_line_starts_in_the_background_without_a_console(self):
        command = autostart.command_line()
        self.assertTrue(command.endswith("--background"))
        self.assertIn("OSSI.pyw", command)
        self.assertIn("pythonw", command.lower())


if __name__ == "__main__":
    unittest.main()
