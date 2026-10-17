import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from app_lifecycle import claim, release, owner_alive


class AppLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_no_app_means_no_owner(self):
        self.assertFalse(owner_alive(self.root))

    def test_live_owner_and_release(self):
        record = claim(self.root)
        self.assertTrue(owner_alive(self.root))
        release(self.root, record)
        self.assertFalse(owner_alive(self.root))

    def test_reused_pid_is_not_owner(self):
        record = claim(self.root)
        fake = Mock()
        fake.create_time.return_value = record['created'] + 10
        fake.exe.return_value = record['exe']
        with patch('app_lifecycle.psutil.Process', return_value=fake):
            self.assertFalse(owner_alive(self.root))

    def test_old_app_cannot_release_new_owner(self):
        record = claim(self.root)
        new = dict(record, token='new-owner')
        (self.root/'app_owner.json').write_text(json.dumps(new))
        release(self.root, record)
        self.assertTrue(owner_alive(self.root))
        self.assertFalse(owner_alive(self.root, record['token']))
        self.assertTrue(owner_alive(self.root, new['token']))

    def test_corrupt_owner_fails_closed(self):
        (self.root/'app_owner.json').write_text('{')
        self.assertFalse(owner_alive(self.root))

    def test_dead_owner_fails_closed(self):
        import psutil
        claim(self.root)
        with patch('app_lifecycle.psutil.Process', side_effect=psutil.NoSuchProcess(123)):
            self.assertFalse(owner_alive(self.root))

    def test_watchdog_cannot_launch_without_app(self):
        import watchdog
        with patch.object(watchdog, 'ROOT', self.root), patch.object(watchdog.subprocess, 'Popen') as launch:
            watchdog.launch('bridge.py', 'server')
        launch.assert_not_called()

    def test_cleanup_stops_both_owned_scripts(self):
        import watchdog
        bridge, telemetry = Mock(), Mock()
        with patch.object(watchdog, 'processes', side_effect=lambda name: [bridge] if name=='bridge.py' else [telemetry]), patch.object(watchdog, 'log'), patch('simulator_link.SimulatorLink') as sc:
            watchdog.stop_children()
        sc.return_value.stop.assert_called_once()
        bridge.terminate.assert_called_once()
        telemetry.terminate.assert_called_once()


if __name__ == '__main__':
    unittest.main()
