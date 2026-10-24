# MAVLink 2 from common.xml

Imported by `wireforge import-mavlink` from minimal.xml, standard.xml, common.xml: 10 messages and 13 enums.

The frame is the one of the published MAVLink 2 model (`wireforge init --protocol mavlink2`):
the 24-bit message id, payloads without their trailing zero bytes and the checksum with the
CRC_EXTRA byte of every message (`app/include/mav2sum/crc_extra.h`, computed from the
definitions). Signed frames are refused. Fields keep their integer types; the enum a field takes
its values from is named in its description. Import the dialect again to follow a new version.

MAVLink is a project of the Dronecode Foundation; this project is not affiliated with or endorsed
by it.
