/**
 * @file config.hpp
 * @brief Build-time configuration of the WireForge runtime.
 *
 * Every option is a macro that can be set on the compiler command line or in a user header named by
 * `WIREFORGE_CONFIG_HEADER`, which is included first.
 *
 * | Macro                     | Effect                                                         |
 * |---------------------------|----------------------------------------------------------------|
 * | `WIREFORGE_CONFIG_HEADER` | Header (with quotes or angle brackets) included before anything |
 * | `WIREFORGE_NO_STD_MUTEX`  | Do not include `<mutex>`; wireforge::StdMutexLock is unavailable |
 * | `WIREFORGE_DEFAULT_LOCK`  | Lock policy type used as wireforge::DefaultLock                 |
 * | `WIREFORGE_CRC_TABLELESS` | Predefined CRC aliases use the bitwise mode instead of a table  |
 */
#ifndef WIREFORGE_CONFIG_HPP
#define WIREFORGE_CONFIG_HPP

#if defined(WIREFORGE_CONFIG_HEADER)
#include WIREFORGE_CONFIG_HEADER
#endif

/// @brief Compile-time view of the configuration macros.
namespace wireforge::config {

#if defined(WIREFORGE_NO_STD_MUTEX)
inline constexpr bool kHasStdMutex = false;
#else
/// @brief true unless `WIREFORGE_NO_STD_MUTEX` is defined.
inline constexpr bool kHasStdMutex = true;
#endif

#if defined(WIREFORGE_CRC_TABLELESS)
inline constexpr bool kCrcUseTable = false;
#else
/// @brief Default table mode of wireforge::Crc; false when `WIREFORGE_CRC_TABLELESS` is defined.
inline constexpr bool kCrcUseTable = true;
#endif

} // namespace wireforge::config

#endif // WIREFORGE_CONFIG_HPP
