/**
 * @file crc_extra.h
 * @brief The CRC_EXTRA byte of every message of the model, in one list for C, C++ and Python.
 *
 * A message added to the model needs its line here: the message id and its CRC_EXTRA byte, a hash
 * of the message definition that the MAVLink tools print (pymavlink:
 * `mavutil.mavlink.MAVLink_<name>_message.crc_extra`). `plugins/mavlink2_crc.py` reads this file.
 */
#ifndef MAV2SUM_CRC_EXTRA_H
#define MAV2SUM_CRC_EXTRA_H

/* clang-format off */
#define MAV2SUM_CRC_EXTRA(X) \
    X(0U, 50U)   /* HEARTBEAT */ \
    X(24U, 24U)  /* GPS_RAW_INT */ \
    X(30U, 39U)  /* ATTITUDE */ \
    X(33U, 104U) /* GLOBAL_POSITION_INT */ \
    X(76U, 152U) /* COMMAND_LONG */ \
    X(77U, 143U) /* COMMAND_ACK */ \
    /* add new messages above this line */
/* clang-format on */

#endif /* MAV2SUM_CRC_EXTRA_H */
