/**
 * @file frame_layout.hpp
 * @brief Positions of fields inside frame headers and trailers.
 */
#ifndef WIREFORGE_FRAME_LAYOUT_HPP
#define WIREFORGE_FRAME_LAYOUT_HPP

#include <cstddef>

namespace wireforge {

/// @brief Position of a header or trailer field; a width of 0 means "no such field".
struct FieldLocation {
    std::size_t offset; ///< Byte offset inside the header (or trailer).
    std::size_t width;  ///< Width in bytes, 0..8.
};

} // namespace wireforge

#endif // WIREFORGE_FRAME_LAYOUT_HPP
