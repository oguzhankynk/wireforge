/* Arm semihosting of QEMU (-semihosting-config enable=on): text on the emulator's console and the
 * exit code of the run. Only for the emulator; real hardware prints through a debug UART. */
#ifndef DRONE_SEMIHOSTING_H
#define DRONE_SEMIHOSTING_H

#include <stdint.h>

static inline uint32_t semihosting_call(uint32_t operation, const void* argument) {
    uint32_t result;
    __asm__ volatile("mov r0, %1\n"
                     "mov r1, %2\n"
                     "bkpt 0xAB\n"
                     "mov %0, r0\n"
                     : "=r"(result)
                     : "r"(operation), "r"(argument)
                     : "r0", "r1", "memory");
    return result;
}

static inline void semihosting_print(const char* text) {
    (void)semihosting_call(0x04U, text); /* SYS_WRITE0 */
}

static inline void semihosting_exit(uint32_t code) {
    const uint32_t block[2] = {0x20026U, code}; /* ADP_Stopped_ApplicationExit */
    (void)semihosting_call(0x20U, block);
    for (;;) {
    }
}

#endif /* DRONE_SEMIHOSTING_H */
