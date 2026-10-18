/**
 * @file fletcher8.h
 * @brief The 8-bit Fletcher checksum of the UBX protocol, for generated C code.
 */
#ifndef UBXSUM_FLETCHER8_H
#define UBXSUM_FLETCHER8_H

#include <stddef.h>
#include <stdint.h>

/**
 * Fletcher-8 over the bytes of a UBX frame without the two sync characters that start it.
 * @return CK_B in the high byte and CK_A in the low byte, as ubxsum::Fletcher8 does.
 */
static inline uint64_t ubxsum_fletcher8(const uint8_t* data, size_t length) {
    uint8_t a = 0U;
    uint8_t b = 0U;
    for (size_t index = 2U; index < length; ++index) {
        a = (uint8_t)(a + data[index]);
        b = (uint8_t)(b + a);
    }
    return ((uint64_t)b << 8U) | a;
}

#endif /* UBXSUM_FLETCHER8_H */
