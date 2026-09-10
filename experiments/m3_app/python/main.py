"""Copy into a duplicate of the working App Lab Blink LED from Python app."""

import logging
import os

from arduino.app_utils import App, Bridge
from led_relay import LedRelay

logging.basicConfig(level=logging.INFO)


def set_led(state):
    Bridge.call("set_led_state", state)


relay = LedRelay(set_led, unix_path=os.environ.get("M3_SOCKET", "/app/m3-led.sock"))
try:
    App.run(user_loop=relay.step)
finally:
    relay.close()
