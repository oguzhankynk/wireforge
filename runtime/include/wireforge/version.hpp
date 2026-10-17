/**
 * @file version.hpp
 * @brief Version of the WireForge runtime.
 */
#ifndef WIREFORGE_VERSION_HPP
#define WIREFORGE_VERSION_HPP

#include <cstdint>

namespace wireforge {

inline constexpr std::uint32_t kVersionMajor = 0U; ///< Major version.
inline constexpr std::uint32_t kVersionMinor = 1U; ///< Minor version.
inline constexpr std::uint32_t kVersionPatch = 0U; ///< Patch version.

/// @brief Packed version number: major * 10000 + minor * 100 + patch.
inline constexpr std::uint32_t kVersionNumber =
    (kVersionMajor * 10000U) + (kVersionMinor * 100U) + kVersionPatch;

} // namespace wireforge

#endif // WIREFORGE_VERSION_HPP
