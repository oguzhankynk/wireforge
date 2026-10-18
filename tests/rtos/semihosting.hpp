// Arm semihosting calls of QEMU (-semihosting): console output and the exit code of the run.
#ifndef WIREFORGE_RTOS_SEMIHOSTING_HPP
#define WIREFORGE_RTOS_SEMIHOSTING_HPP

#include <cstdint>

namespace rtos {

constexpr std::uint32_t kWriteChar = 0x03U;
constexpr std::uint32_t kExitExtended = 0x20U;
constexpr std::uint32_t kApplicationExit = 0x20026U;

inline std::uint32_t call(std::uint32_t operation, const void* argument) noexcept {
    std::uint32_t result = 0U;
    __asm__ volatile("mov r0, %1\n"
                     "mov r1, %2\n"
                     "bkpt 0xAB\n"
                     "mov %0, r0\n"
                     : "=r"(result)
                     : "r"(operation), "r"(argument)
                     : "r0", "r1", "memory");
    return result;
}

inline void write_char(char value) noexcept {
    call(kWriteChar, &value);
}

[[noreturn]] inline void exit_with(std::uint32_t code) noexcept {
    const std::uint32_t block[2] = {kApplicationExit, code};
    call(kExitExtended, &block[0]);
    for (;;) {
    }
}

} // namespace rtos

#endif
