/**
 * @file limits.hpp
 * @brief Example application header providing constants used by the WireForge model.
 */
#ifndef APP_LIMITS_HPP
#define APP_LIMITS_HPP

#include <cstddef>

/// @brief Example application namespace.
namespace app {

#if defined(PLATFORM_B)
inline constexpr std::size_t kMaxFrameBytes = 192U; ///< Largest frame of the link driver.
#else
inline constexpr std::size_t kMaxFrameBytes = 128U; ///< Largest frame of the link driver.
#endif

} // namespace app

#endif // APP_LIMITS_HPP
