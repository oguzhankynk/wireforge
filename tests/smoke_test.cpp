#include "support/alloc_trap.hpp"
#include "wireforge/version.hpp"

#include <gtest/gtest.h>
#include <new>

namespace {

TEST(Smoke, VersionNumberMatchesComponents) {
    EXPECT_EQ(wireforge::kVersionNumber, 100U);
}

TEST(AllocationTrap, DetectsHeapUse) {
    const wireforge::testing::AllocationTrap trap;
    void* block = ::operator new(16U);
    ::operator delete(block);
    EXPECT_EQ(trap.allocations(), 1U);
}

TEST(AllocationTrap, StaysZeroWithoutHeapUse) {
    const wireforge::testing::AllocationTrap trap;
    EXPECT_EQ(wireforge::kVersionNumber, 100U);
    EXPECT_EQ(trap.allocations(), 0U);
}

} // namespace
