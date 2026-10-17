#include "support/alloc_trap.hpp"
#include "wireforge/lock.hpp"
#include "wireforge/span.hpp"
#include "wireforge/static_vector.hpp"
#include "wireforge/status.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <gtest/gtest.h>
#include <numeric>
#include <string_view>
#include <thread>
#include <type_traits>

namespace {

using wireforge::FixedString;
using wireforge::Span;
using wireforge::StaticVector;
using wireforge::Status;

constexpr std::size_t compile_time_sum() {
    StaticVector<int, 4> values;
    if (values.push_back(3) != Status::Ok || values.push_back(4) != Status::Ok) {
        return 0U;
    }
    return static_cast<std::size_t>(values[0] + values[1]) + values.size();
}
static_assert(compile_time_sum() == 9U, "StaticVector is usable in constant expressions");

constexpr std::size_t compile_time_string() {
    FixedString<8> text;
    return text.assign("abc") == Status::Ok ? text.size() : 0U;
}
static_assert(compile_time_string() == 3U, "FixedString is usable in constant expressions");

static_assert(std::is_constructible_v<Span<const int>, Span<int>>, "adds const");
static_assert(!std::is_constructible_v<Span<int>, Span<const int>>, "never drops const");

TEST(Span, ViewsAndSubspans) {
    ::testing::Test::RecordProperty("requirements", "RTR-span-001");
    std::array<std::uint8_t, 5> bytes{1U, 2U, 3U, 4U, 5U};
    const Span<std::uint8_t> view(bytes);
    EXPECT_EQ(view.size(), 5U);
    EXPECT_FALSE(view.empty());
    EXPECT_EQ(view[4], 5U);
    view[0] = 9U;
    EXPECT_EQ(bytes.at(0), 9U);
    EXPECT_EQ(view.subspan(1U, 2U).size(), 2U);
    EXPECT_EQ(view.subspan(1U, 2U)[0], 2U);
    EXPECT_EQ(view.subspan(3U).size(), 2U);
    EXPECT_EQ(view.subspan(4U, 10U).size(), 1U);
    EXPECT_TRUE(view.subspan(9U).empty());
    EXPECT_EQ(view.first(2U).size(), 2U);
}

TEST(Span, ConstConversionAndHelpers) {
    ::testing::Test::RecordProperty("requirements", "RTR-span-002");
    std::array<std::uint8_t, 5> bytes{9U, 2U, 3U, 4U, 5U};
    const Span<const std::uint8_t> read_only{Span<std::uint8_t>(bytes)};
    const std::size_t total = std::accumulate(read_only.begin(), read_only.end(), std::size_t{0U});
    EXPECT_EQ(total, 9U + 2U + 3U + 4U + 5U);
    EXPECT_TRUE(Span<int>().empty());
    const std::array<int, 2> constant{7, 8};
    EXPECT_EQ((wireforge::make_span(constant)[1]), 8);
    EXPECT_EQ(wireforge::make_span(bytes).data(), bytes.data());
}

TEST(StaticVector, PushResizeAndCapacity) {
    ::testing::Test::RecordProperty("requirements", "RTR-static_vector-001");
    StaticVector<std::uint16_t, 3> values;
    EXPECT_EQ(values.capacity(), 3U);
    EXPECT_TRUE(values.empty());
    ASSERT_EQ(values.push_back(10U), Status::Ok);
    ASSERT_EQ(values.push_back(20U), Status::Ok);
    ASSERT_EQ(values.push_back(30U), Status::Ok);
    EXPECT_TRUE(values.full());
    EXPECT_EQ(values.push_back(40U), Status::CountExceedsMax);
    EXPECT_EQ(values.size(), 3U);
    EXPECT_EQ(values.resize(4U), Status::CountExceedsMax);
    ASSERT_EQ(values.resize(1U), Status::Ok);
    ASSERT_EQ(values.resize(3U), Status::Ok);
    EXPECT_EQ(values[0], 10U);
    EXPECT_EQ(values[1], 0U) << "regrown elements are value-initialized";
    values[2] = 7U;
    const auto& const_values = values;
    EXPECT_EQ(const_values[2], 7U);
    EXPECT_EQ(const_values.span().size(), 3U);
    EXPECT_EQ(values.span().data(), values.data());
    EXPECT_EQ(const_values.end() - const_values.begin(), 3);
    values.clear();
    EXPECT_TRUE(values.empty());
    EXPECT_EQ(values.begin(), values.end());
}

TEST(StaticVector, OptionalFieldIsCapacityOne) {
    ::testing::Test::RecordProperty("requirements", "RTR-static_vector-002");
    StaticVector<double, 1> optional;
    EXPECT_TRUE(optional.empty());
    ASSERT_EQ(optional.push_back(1.5), Status::Ok);
    EXPECT_EQ(optional.push_back(2.5), Status::CountExceedsMax);
    EXPECT_EQ(optional[0], 1.5);
}

TEST(StaticVector, EqualityIgnoresUnusedStorage) {
    ::testing::Test::RecordProperty("requirements", "RTR-static_vector-003");
    StaticVector<int, 4> lhs;
    StaticVector<int, 4> rhs;
    ASSERT_EQ(lhs.push_back(1), Status::Ok);
    ASSERT_EQ(rhs.push_back(1), Status::Ok);
    ASSERT_EQ(rhs.push_back(2), Status::Ok);
    EXPECT_NE(lhs, rhs);
    rhs.clear();
    ASSERT_EQ(rhs.push_back(1), Status::Ok);
    EXPECT_EQ(lhs, rhs);
    rhs[0] = 5;
    EXPECT_NE(lhs, rhs);
}

TEST(FixedString, AssignResizeAndView) {
    ::testing::Test::RecordProperty("requirements", "RTR-static_vector-004");
    FixedString<5> text;
    EXPECT_EQ(text.capacity(), 5U);
    EXPECT_TRUE(text.empty());
    EXPECT_STREQ(text.c_str(), "");
    ASSERT_EQ(text.assign("hello"), Status::Ok);
    EXPECT_EQ(text.view(), std::string_view("hello"));
    EXPECT_EQ(text.assign("toolong"), Status::CountExceedsMax);
    EXPECT_EQ(text.view(), std::string_view("hello")) << "failed assign keeps the content";
    ASSERT_EQ(text.resize(2U), Status::Ok);
    EXPECT_STREQ(text.c_str(), "he");
    ASSERT_EQ(text.resize(4U), Status::Ok);
    EXPECT_EQ(text.size(), 4U);
    EXPECT_EQ(text[3], '\0');
    text[2] = 'y';
    const auto& const_text = text;
    EXPECT_EQ(const_text[2], 'y');
    EXPECT_EQ(const_text.data(), text.c_str());
    EXPECT_EQ(text.resize(6U), Status::CountExceedsMax);
    text[0] = 'H';
    EXPECT_EQ(text.view().front(), 'H');
    FixedString<5> other;
    ASSERT_EQ(other.assign(text.view()), Status::Ok);
    EXPECT_EQ(other, text);
    other.clear();
    EXPECT_NE(other, text);
    EXPECT_STREQ(other.c_str(), "");
}

struct BadLock {
    static void lock() {} // not noexcept
    static void unlock() noexcept {}
};
struct NoUnlock {
    static void lock() noexcept {}
};
static_assert(wireforge::is_lock_policy_v<wireforge::NullLock>, "NullLock");
static_assert(!wireforge::is_lock_policy_v<BadLock>, "lock() must be noexcept");
static_assert(!wireforge::is_lock_policy_v<NoUnlock>, "unlock() is required");
static_assert(!wireforge::is_lock_policy_v<int>, "not a lock");
static_assert(std::is_same_v<wireforge::DefaultLock, wireforge::StdMutexLock>,
              "StdMutexLock is the default lock");

template <typename Lock> void hammer_counter(std::uint32_t& counter) {
    Lock lock;
    constexpr int kIterations = 2000;
    const auto work = [&lock, &counter]() {
        for (int index = 0; index < kIterations; ++index) {
            const wireforge::ScopedLock<Lock> guard(lock);
            ++counter;
        }
    };
    std::thread first(work);
    std::thread second(work);
    first.join();
    second.join();
}

TEST(Lock, StdMutexLockSerializesThreads) {
    ::testing::Test::RecordProperty("requirements", "RTR-lock-001");
    std::uint32_t counter = 0U;
    hammer_counter<wireforge::StdMutexLock>(counter);
    EXPECT_EQ(counter, 4000U);
}

TEST(Lock, NullLockGuardIsUsable) {
    ::testing::Test::RecordProperty("requirements", "RTR-lock-002");
    wireforge::NullLock lock;
    std::uint32_t counter = 0U;
    {
        const wireforge::ScopedLock<wireforge::NullLock> guard(lock);
        ++counter;
    }
    EXPECT_EQ(counter, 1U);
}

TEST(Lock, RelaxedCounterAcrossThreads) {
    ::testing::Test::RecordProperty("requirements", "RTR-lock-003");
    wireforge::RelaxedCounter counter;
    const auto work = [&counter]() {
        for (int index = 0; index < 1000; ++index) {
            counter.increment();
        }
        counter.add(5U);
    };
    std::thread first(work);
    std::thread second(work);
    first.join();
    second.join();
    EXPECT_EQ(counter.load(), 2010U);
    counter.reset();
    EXPECT_EQ(counter.load(), 0U);
}

TEST(Containers, NoHeapAllocation) {
    ::testing::Test::RecordProperty("requirements", "RTR-static_vector-005");
    const wireforge::testing::AllocationTrap trap;
    StaticVector<std::uint32_t, 64> values;
    for (std::uint32_t index = 0U; index < 64U; ++index) {
        ASSERT_EQ(values.push_back(index), Status::Ok);
    }
    FixedString<16> text;
    ASSERT_EQ(text.assign("no heap here"), Status::Ok);
    wireforge::StdMutexLock lock;
    {
        const wireforge::ScopedLock<wireforge::StdMutexLock> guard(lock);
        ASSERT_EQ(values.resize(10U), Status::Ok);
    }
    EXPECT_EQ(trap.allocations(), 0U);
}

} // namespace
