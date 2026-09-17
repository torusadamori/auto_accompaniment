"""WinMM shutdown sequencing and narrowly scoped safe error handling."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from autoaccomp.midi_io import WINMM_UNPREPARE_ERROR, close_winmm_input


class ShutdownTests(unittest.TestCase):
    def port(self):
        native = Mock(is_deleted=False)
        return SimpleNamespace(closed=False, _rt=native, poll=Mock(return_value=None))

    def test_cancel_drain_settle_close_delete_once(self):
        port = self.port()
        calls = Mock()
        calls.attach_mock(port._rt, "native")
        calls.attach_mock(port.poll, "poll")
        with patch("autoaccomp.midi_io.time.sleep") as settle:
            calls.attach_mock(settle, "settle")
            close_winmm_input(port)
            close_winmm_input(port)
        self.assertEqual([c[0] for c in calls.mock_calls],
                         ["native.ignore_types", "native.cancel_callback", "poll", "settle",
                          "native.close_port", "native.delete"])
        self.assertTrue(port.closed)
        settle.assert_called_once_with(0.05)

    def test_already_disposed_is_idempotent(self):
        port = self.port()
        port._rt.is_deleted = True
        close_winmm_input(port)
        self.assertTrue(port.closed)
        port._rt.close_port.assert_not_called()
        port._rt.delete.assert_not_called()

    def test_known_error_only_safe_after_confirmed_disposal(self):
        port = self.port()

        def disposed():
            port._rt.is_deleted = True
            raise RuntimeError(WINMM_UNPREPARE_ERROR)

        port._rt.close_port.side_effect = disposed
        with patch("autoaccomp.midi_io.time.sleep"):
            close_winmm_input(port)
        self.assertTrue(port.closed)
        port._rt.delete.assert_not_called()

    def test_not_open_flag_does_not_prove_safe_native_close(self):
        port = self.port()
        port._rt.is_port_open.return_value = False
        port._rt.close_port.side_effect = RuntimeError(WINMM_UNPREPARE_ERROR)
        with patch("autoaccomp.midi_io.time.sleep"), self.assertRaisesRegex(RuntimeError, "midiInUnprepareHeader"):
            close_winmm_input(port)
        self.assertFalse(port.closed)
        port._rt.delete.assert_not_called()

    def test_unknown_close_and_delete_errors_propagate(self):
        for method in ("close_port", "delete"):
            port = self.port()
            getattr(port._rt, method).side_effect = OSError("driver failed")
            with patch("autoaccomp.midi_io.time.sleep"), self.assertRaisesRegex(OSError, "driver failed"):
                close_winmm_input(port)
            self.assertFalse(port.closed)

    def test_drain_is_bounded_even_if_input_keeps_arriving(self):
        port = self.port()
        port.poll.return_value = object()
        with patch("autoaccomp.midi_io.time.sleep"):
            close_winmm_input(port)
        self.assertEqual(port.poll.call_count, 4096)
        self.assertTrue(port.closed)
