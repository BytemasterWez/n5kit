import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from n5kit import host

class HostTests(unittest.TestCase):
    def test_window_edges_and_fit(self):
        for minute, ok in [(4, False), (5, True), (51, True), (52, False)]:
            now = dt.datetime(2026, 10, 10, 12, minute, tzinfo=dt.timezone.utc)
            if ok:
                host.window(now, 1)
            else:
                with self.assertRaisesRegex(RuntimeError, 'snapshot_window'): host.window(now, 1)
        with self.assertRaises(RuntimeError): host.window(now.replace(minute=51), 60)

    def test_strict_floors_and_unreadable(self):
        for value in (16, 15):
            with patch.object(host, 'metrics', return_value={'memory': value, 'docker': 300, 'work': 150, 'load': 0}):
                with self.assertRaises(RuntimeError): host.probe()
        with patch.object(host, 'metrics', return_value={'memory': 17, 'docker': 300, 'work': 150, 'load': 0}):
            self.assertEqual(host.probe()['memory'], 17)
        with patch.object(host, 'metrics', side_effect=OSError):
            with self.assertRaises(RuntimeError): host.probe()

    def test_custom_floor_cannot_disable_another_guard(self):
        with patch.object(host, 'metrics', return_value={'memory':30,'docker':256,'work':150,'load':0}):
            with self.assertRaises(RuntimeError): host.probe({'memory':20})
        with patch.object(host, 'metrics', return_value={'memory':30,'docker':300,'work':150,'load':0}):
            with self.assertRaises(RuntimeError): host.probe({'memory':1})

    def test_each_disk_floor_and_load_equality(self):
        for key, value in [('docker', 256), ('work', 100), ('load', 8)]:
            values = {'memory':17, 'docker':300, 'work':150, 'load':0}
            values[key] = value
            with patch.object(host, 'metrics', return_value=values), patch.object(host.os, 'cpu_count', return_value=4):
                with self.assertRaises(RuntimeError): host.probe()

    def test_expiring_memory_exception(self):
        now = dt.datetime(2026, 10, 10, 22, tzinfo=dt.timezone.utc)
        exception = {'authority': 'Wesley approved #0063', 'card': '#0227', 'expiry': '2026-10-11T00:00:00+00:00'}
        values = {'memory': 11, 'docker': 300, 'work': 150, 'load': 0}
        with patch.object(host, 'metrics', return_value=values), patch.object(host.dt, 'datetime', wraps=dt.datetime) as clock:
            clock.now.return_value = now
            for floor in (8, 10): self.assertEqual(host.probe({'memory': floor}, exception)['memory'], 11)
            bad = [None, {}, 'invalid', dict(exception, authority=' '), dict(exception, card=''), dict(exception, expiry='invalid'), dict(exception, expiry='2026-10-11T00:00:01Z'), dict(exception, expiry='2026-10-10T22:00:00Z'), dict(exception, expiry='2026-10-10T21:59:59Z'), dict(exception, expiry='2026-10-11T00:00:00')]
            bad += [{k: v for k, v in exception.items() if k != field} for field in ('authority', 'card', 'expiry')]
            for ex in bad:
                with self.subTest(exception=ex), self.assertRaises(RuntimeError): host.probe({'memory': 10}, ex)
            for floors in (None, {'memory': 7.99}, {'memory': 8, 'docker': 255}, {'memory': 8, 'work': 99}):
                with self.subTest(floors=floors), self.assertRaises(RuntimeError): host.probe(floors, exception)
            values['memory'] = 8
            with self.assertRaises(RuntimeError): host.probe({'memory': 8}, exception)
            values['memory'] = 17
            self.assertEqual(host.probe()['memory'], 17)
            clock.now.return_value = now + dt.timedelta(hours=2)
            with self.assertRaises(RuntimeError): host.probe({'memory': 10}, exception)

    def test_booking_race_has_exactly_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'HOST-RESERVATIONS.md'
            path.write_text('')
            def attempt(token):
                try:
                    host.book(token,60,path)
                    return True
                except RuntimeError:
                    return False
            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(sum(pool.map(attempt,['a'*32,'b'*32])),1)

    def test_booking_and_owned_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'HOST-RESERVATIONS.md'
            path.write_text('| Owner | Card | Start (BST) | Expected end | Load |\n|---|---|---|---|---|\n')
            host.book('a'*32, 60, path)
            with self.assertRaises(RuntimeError): host.book('b'*32, 60, path)
            host.release('b'*32, path)
            self.assertIn('a'*32, path.read_text())
            host.release('a'*32, path)
            self.assertNotIn('a'*32, path.read_text())

if __name__ == '__main__': unittest.main()
