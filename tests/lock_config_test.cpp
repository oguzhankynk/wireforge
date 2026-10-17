// Compiled with WIREFORGE_NO_STD_MUTEX: the runtime must build without <mutex>.
#include "wireforge/lock.hpp"
#include "wireforge/ring_buffer.hpp"

#include <array>
#include <cstdint>
#include <gtest/gtest.h>
#include <type_traits>

#if !defined(WIREFORGE_NO_STD_MUTEX)
#error "this test must be compiled with WIREFORGE_NO_STD_MUTEX"
#endif

namespace {

static_assert(std::is_same_v<wireforge::DefaultLock, wireforge::NullLock>,
              "without <mutex> the default lock is NullLock unless WIREFORGE_DEFAULT_LOCK is set");
static_assert(!wireforge::config::kHasStdMutex, "config reflects the macro");

TEST(LockConfig, RingBuildsWithoutStdMutex) {
    std::array<std::uint8_t, 4> storage{};
    wireforge::LockedByteRing<> ring{wireforge::Span<std::uint8_t>(storage)};
    const std::array<std::uint8_t, 2> bytes{1U, 2U};
    EXPECT_EQ(ring.write(wireforge::Span<const std::uint8_t>(bytes)), 2U);
    EXPECT_EQ(ring.size(), 2U);
}

} // namespace
