# SAE J1939: the frame identifier and three broadcast parameter groups (EEC1, CCVS, ET1)

Source: the public descriptions of SAE J1939 (the 29-bit identifier of J1939-21, the parameter groups and signals of
J1939-71). The model is written from those descriptions; no text or table of the standards is copied. The standards
themselves are not free: check the signals against your copy before you rely on them.

Covered: the identifier as the header of a SocketCAN frame and the groups EEC1 (PGN 61444: engine speed, torques, mode,
starter mode), CCVS (PGN 65265: parking brake switch, wheel-based vehicle speed) and ET1 (PGN 65262: coolant, fuel and
oil temperature). Signals have `scale` and `offset`; one-byte signals stop at 250, because 251 to 255 are not data.
Unused bits and bytes are written as ones.

Checked against an independent implementation: `cantools` packs the identifier (`cantools.j1939`) and encodes the data
of the same groups from a DBC file of the same signals, byte for byte (`tests/python/test_j1939.py`). The DBC file is
in `tests/python/dbc/j1939.dbc`; `wireforge import-dbc` turned it into the first version of the messages.

How the model says what J1939 does:
- A header field cannot be a bit-field, so the four bytes of the identifier word are three fields: `control` (extended
  frame, remote, error, priority 3 bits, reserved bit, data page), `pduFormatAndSpecific` (the low 16 bits of the PGN,
  which is the message id) and `sourceAddress`. The three groups are data page 0, so the message id identifies them.

Not covered: groups of data page 1, destination-specific groups (PDU format below 240, whose PDU specific field is an
address), the transport protocol (messages longer than 8 bytes, such as DM1 with several faults), address claiming and
the diagnostic messages.

SAE and J1939 are marks of SAE International; this project is not affiliated with or endorsed by it.
