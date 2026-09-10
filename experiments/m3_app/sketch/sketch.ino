// SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
// SPDX-License-Identifier: MPL-2.0
// Adapted from Arduino's Blink LED from Python example; initialize OFF.
#include <Arduino_RouterBridge.h>

void set_led_state(bool state) {
    digitalWrite(LED_BUILTIN, state ? LOW : HIGH);
}

void setup() {
    pinMode(LED_BUILTIN, OUTPUT);
    set_led_state(false);
    Bridge.begin();
    Bridge.provide("set_led_state", set_led_state);
}

void loop() {}
