/**
 * @file limits.h
 * @brief Example application header providing constants used by the WireForge model in C.
 */
#ifndef APP_LIMITS_H
#define APP_LIMITS_H

/* Largest frame of the link driver. */
#if defined(PLATFORM_B)
#define APP_MAX_FRAME_BYTES 192U
#else
#define APP_MAX_FRAME_BYTES 128U
#endif

#endif /* APP_LIMITS_H */
