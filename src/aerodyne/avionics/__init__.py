"""Avionics: sensor framework and validation, navigation / state estimation,
flight state machine, telemetry protocol, flight data logging, firmware
identity and automated preflight.

These Python modules are the *reference implementation* and test oracle for
the C flight software in ``firmware/``; both implement the same state machine
thresholds and the same telemetry wire format, and the SIL tests check that
they agree.
"""
