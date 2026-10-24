/**
 * @file crc_extra.h
 * @brief The CRC_EXTRA byte of every message, one list for C, C++ and Python.
 *
 * Written by `wireforge import-mavlink` from common.xml. A message added to
 * the model needs its line here; `plugins/mavlink2_crc.py` reads this file.
 */
#ifndef MAV2SUM_CRC_EXTRA_H
#define MAV2SUM_CRC_EXTRA_H

/* clang-format off */
#define MAV2SUM_CRC_EXTRA(X) \
    X(0U, 50U) /* HEARTBEAT */ \
    X(1U, 124U) /* SYS_STATUS */ \
    X(22U, 220U) /* PARAM_VALUE */ \
    X(24U, 24U) /* GPS_RAW_INT */ \
    X(31U, 246U) /* ATTITUDE_QUATERNION */ \
    X(73U, 38U) /* MISSION_ITEM_INT */ \
    X(76U, 152U) /* COMMAND_LONG */ \
    X(77U, 143U) /* COMMAND_ACK */ \
    X(131U, 223U) /* ENCAPSULATED_DATA */ \
    X(253U, 83U) /* STATUSTEXT */ \
    /* add new messages above this line */
/* clang-format on */

#endif /* MAV2SUM_CRC_EXTRA_H */
