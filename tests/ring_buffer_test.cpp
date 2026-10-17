#include "support/alloc_trap.hpp"
#include "wireforge/bit_utils.hpp"
#include "wireforge/lock.hpp"
#include "wireforge/ring_buffer.hpp"
#include "wireforge/span.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <functional>
#include <gtest/gtest.h>
#include <numeric>
#include <thread>
#include <vector>

namespace {

using wireforge::LockedByteRing;
using wireforge::Span;
using wireforge::SpscByteRing;
using wireforge::StaticSpscByteRing;

template <std::size_t N> Span<const std::uint8_t> view(const std::array<std::uint8_t, N>& bytes) {
    return Span<const std::uint8_t>(bytes);
}

TEST(SpscByteRing, FullCapacityIsUsable) {
    ::testing::Test::RecordProperty("requirements", "RTR-ring_buffer-001");
    std::array<std::uint8_t, 4> storage{};
    SpscByteRing ring{Span<std::uint8_t>(storage)};
    EXPECT_EQ(ring.capacity(), 4U);
    EXPECT_TRUE(ring.empty());
    const std::array<std::uint8_t, 6> bytes{1U, 2U, 3U, 4U, 5U, 6U};
    EXPECT_EQ(ring.write(view(bytes)), 4U) << "partial write when full";
    EXPECT_EQ(ring.size(), 4U);
    EXPECT_EQ(ring.free_space(), 0U);
    EXPECT_EQ(ring.write(view(bytes)), 0U);
    std::array<std::uint8_t, 4> out{};
    EXPECT_EQ(ring.read(Span<std::uint8_t>(out)), 4U);
    EXPECT_EQ(out, (std::array<std::uint8_t, 4>{1U, 2U, 3U, 4U}));
    EXPECT_TRUE(ring.empty());
}

TEST(SpscByteRing, WrapAroundPreservesOrder) {
    ::testing::Test::RecordProperty("requirements", "RTR-ring_buffer-002");
    std::array<std::uint8_t, 5> storage{};
    SpscByteRing ring{Span<std::uint8_t>(storage)};
    std::uint8_t next_in = 0U;
    std::uint8_t next_out = 0U;
    for (int round = 0; round < 50; ++round) {
        std::array<std::uint8_t, 3> chunk{};
        std::iota(chunk.begin(), chunk.end(), next_in);
        next_in = static_cast<std::uint8_t>(next_in + chunk.size());
        ASSERT_EQ(ring.write(view(chunk)), 3U);
        std::array<std::uint8_t, 3> out{};
        ASSERT_EQ(ring.read(Span<std::uint8_t>(out)), 3U);
        for (const std::uint8_t byte : out) {
            EXPECT_EQ(byte, next_out++);
        }
    }
}

TEST(SpscByteRing, PeekWithOffsetAndConsume) {
    ::testing::Test::RecordProperty("requirements", "RTR-ring_buffer-003");
    std::array<std::uint8_t, 6> storage{};
    SpscByteRing ring{Span<std::uint8_t>(storage)};
    const std::array<std::uint8_t, 4> prefix{9U, 9U, 9U, 9U};
    ASSERT_EQ(ring.write(view(prefix)), 4U);
    ASSERT_EQ(ring.consume(4U), 4U); // move the window so the next data wraps
    const std::array<std::uint8_t, 5> bytes{10U, 11U, 12U, 13U, 14U};
    ASSERT_EQ(ring.write(view(bytes)), 5U);
    std::array<std::uint8_t, 3> out{};
    EXPECT_EQ(ring.peek(Span<std::uint8_t>(out), 2U), 3U);
    EXPECT_EQ(out, (std::array<std::uint8_t, 3>{12U, 13U, 14U}));
    EXPECT_EQ(ring.peek(Span<std::uint8_t>(out), 5U), 0U);
    EXPECT_EQ(ring.peek(Span<std::uint8_t>(out), 4U), 1U);
    EXPECT_EQ(ring.size(), 5U) << "peek does not consume";
    EXPECT_EQ(ring.consume(2U), 2U);
    EXPECT_EQ(ring.peek(Span<std::uint8_t>(out)), 3U);
    EXPECT_EQ(out.at(0), 12U);
    EXPECT_EQ(ring.consume(10U), 3U);
    EXPECT_TRUE(ring.empty());
    ASSERT_EQ(ring.write(view(bytes)), 5U);
    ring.reset();
    EXPECT_TRUE(ring.empty());
}

// Deterministic xorshift32 so failures reproduce (no seeding from the environment).
class XorShift32 {
public:
    std::size_t next(std::size_t bound) noexcept {
        state_ ^= state_ << 13U;
        state_ ^= state_ >> 17U;
        state_ ^= state_ << 5U;
        return state_ % bound;
    }

private:
    std::uint32_t state_ = 2463534242U;
};

// Applies random operations to a ring and to a std::deque model (the model may allocate; the ring
// never does) and checks that both agree after every step.
class RingModelCheck {
public:
    explicit RingModelCheck(Span<std::uint8_t> storage) : ring_(storage) {}

    void write(std::size_t count) {
        std::array<std::uint8_t, kMaxChunk> chunk{};
        for (std::size_t index = 0U; index < count; ++index) {
            chunk.at(index) = static_cast<std::uint8_t>(next_ + index);
        }
        const std::size_t written = ring_.write(Span<const std::uint8_t>(chunk.data(), count));
        ASSERT_EQ(written, std::min(count, ring_.capacity() - model_.size()));
        for (std::size_t index = 0U; index < written; ++index) {
            model_.push_back(chunk.at(index));
        }
        next_ = static_cast<std::uint8_t>(next_ + written);
    }

    void peek(std::size_t count, std::size_t offset) {
        std::array<std::uint8_t, kMaxChunk> out{};
        const std::size_t copied = ring_.peek(Span<std::uint8_t>(out.data(), count), offset);
        const std::size_t available = model_.size() > offset ? model_.size() - offset : 0U;
        ASSERT_EQ(copied, std::min(count, available));
        // An offset past the end copies nothing; clamp it so the model iterator stays in range
        // (debug iterators, such as MSVC's, reject seeking past end()).
        const std::size_t start = std::min(offset, model_.size());
        EXPECT_TRUE(std::equal(out.begin(),
                               std::next(out.begin(), static_cast<std::ptrdiff_t>(copied)),
                               std::next(model_.begin(), static_cast<std::ptrdiff_t>(start))));
    }

    void read(std::size_t count) {
        std::array<std::uint8_t, kMaxChunk> out{};
        const std::size_t copied = ring_.read(Span<std::uint8_t>(out.data(), count));
        ASSERT_EQ(copied, std::min(count, model_.size()));
        const auto end = std::next(model_.begin(), static_cast<std::ptrdiff_t>(copied));
        EXPECT_TRUE(std::equal(model_.begin(), end, out.begin()));
        model_.erase(model_.begin(), end);
    }

    void consume(std::size_t count) {
        const std::size_t discarded = ring_.consume(count);
        ASSERT_EQ(discarded, std::min(count, model_.size()));
        model_.erase(model_.begin(),
                     std::next(model_.begin(), static_cast<std::ptrdiff_t>(discarded)));
    }

    [[nodiscard]] bool sizes_agree() const { return ring_.size() == model_.size(); }

    static constexpr std::size_t kMaxChunk = 9U;

private:
    SpscByteRing ring_;
    std::deque<std::uint8_t> model_;
    std::uint8_t next_ = 0U;
};

TEST(SpscByteRing, RandomOperationsMatchModel) {
    ::testing::Test::RecordProperty("requirements", "RTR-ring_buffer-004");
    std::array<std::uint8_t, 7> storage{};
    RingModelCheck check{Span<std::uint8_t>(storage)};
    XorShift32 random;
    for (int step = 0; step < 20000; ++step) {
        const std::size_t count = random.next(RingModelCheck::kMaxChunk + 1U);
        switch (random.next(4U)) {
        case 0U:
            check.write(count);
            break;
        case 1U:
            check.peek(count, random.next(3U));
            break;
        case 2U:
            check.read(count);
            break;
        default:
            check.consume(count);
            break;
        }
        ASSERT_TRUE(check.sizes_agree()) << "step " << step;
    }
}

TEST(SpscByteRing, ProducerConsumerStress) {
    ::testing::Test::RecordProperty("requirements", "RTR-ring_buffer-005");
    StaticSpscByteRing<61> ring;
    constexpr std::uint32_t kTotal = 200000U;
    std::thread producer([&ring]() {
        std::uint32_t sent = 0U;
        while (sent < kTotal) {
            std::array<std::uint8_t, 13> chunk{};
            std::size_t count = 0U;
            for (std::uint8_t& byte : chunk) {
                if (sent + count == kTotal) {
                    break;
                }
                byte = static_cast<std::uint8_t>((sent + count) & 0xFFU);
                ++count;
            }
            sent += wireforge::narrow_to<std::uint32_t>(
                ring.write(Span<const std::uint8_t>(chunk.data(), count)));
        }
    });
    std::uint32_t received = 0U;
    bool in_order = true;
    while (received < kTotal) {
        std::array<std::uint8_t, 17> out{};
        const std::size_t count = ring.read(Span<std::uint8_t>(out));
        for (std::size_t index = 0U; index < count; ++index) {
            in_order = in_order && out.at(index) == static_cast<std::uint8_t>(received & 0xFFU);
            ++received;
        }
    }
    producer.join();
    EXPECT_TRUE(in_order);
    EXPECT_TRUE(ring.empty());
}

// Several producers write 4-byte records atomically; several consumers read whole records. Every
// record must arrive intact exactly once.
constexpr std::uint32_t kPerProducer = 5000U;
constexpr std::uint32_t kProducers = 3U;
constexpr std::uint32_t kRecords = kPerProducer * kProducers;

struct ConsumerTotals {
    std::atomic<std::uint32_t> consumed{0U};
    std::atomic<std::uint64_t> sum{0U};
    std::atomic<std::uint32_t> corrupt{0U};
};

void produce_records(LockedByteRing<>& ring, std::uint32_t producer) {
    for (std::uint32_t index = 0U; index < kPerProducer; ++index) {
        const std::array<std::uint8_t, 4> record{0xA5U, static_cast<std::uint8_t>(producer),
                                                 static_cast<std::uint8_t>(index >> 8U),
                                                 static_cast<std::uint8_t>(index & 0xFFU)};
        while (!ring.write_all(view(record))) {
            std::this_thread::yield();
        }
    }
}

void consume_records(LockedByteRing<>& ring, ConsumerTotals& totals) {
    while (totals.consumed.load() < kRecords) {
        std::array<std::uint8_t, 4> record{};
        // read() of 4 bytes runs under the lock, and producers only store whole records.
        if (ring.size() < record.size() || ring.read(Span<std::uint8_t>(record)) != 4U) {
            std::this_thread::yield();
            continue;
        }
        if (record.at(0) != 0xA5U) {
            ++totals.corrupt;
        }
        totals.sum += (std::uint64_t{record.at(1)} << 16U) | (std::uint64_t{record.at(2)} << 8U) |
                      record.at(3);
        ++totals.consumed;
    }
}

TEST(LockedByteRing, MultiProducerMultiConsumerStress) {
    ::testing::Test::RecordProperty("requirements", "RTR-ring_buffer-006");
    std::array<std::uint8_t, 64> storage{};
    LockedByteRing<> ring{Span<std::uint8_t>(storage)};
    ConsumerTotals totals;
    std::vector<std::thread> threads;
    threads.reserve(kProducers + 2U);
    for (std::uint32_t producer = 0U; producer < kProducers; ++producer) {
        threads.emplace_back(produce_records, std::ref(ring), producer);
    }
    threads.emplace_back(consume_records, std::ref(ring), std::ref(totals));
    threads.emplace_back(consume_records, std::ref(ring), std::ref(totals));
    for (std::thread& thread : threads) {
        thread.join();
    }
    std::uint64_t expected = 0U;
    for (std::uint64_t producer = 0U; producer < kProducers; ++producer) {
        // sum of (producer << 16 | index) over index = 0 .. kPerProducer - 1
        expected += (producer << 16U) * kPerProducer + (kPerProducer * (kPerProducer - 1U) / 2U);
    }
    EXPECT_EQ(totals.corrupt.load(), 0U);
    EXPECT_EQ(totals.consumed.load(), kRecords);
    EXPECT_EQ(totals.sum.load(), expected);
    EXPECT_TRUE(ring.empty());
}

TEST(LockedByteRing, ApiMirrorsSpsc) {
    ::testing::Test::RecordProperty("requirements", "RTR-ring_buffer-006");
    std::array<std::uint8_t, 4> storage{};
    LockedByteRing<wireforge::NullLock> ring{Span<std::uint8_t>(storage)};
    const std::array<std::uint8_t, 3> bytes{1U, 2U, 3U};
    EXPECT_EQ(ring.capacity(), 4U);
    EXPECT_EQ(ring.write(view(bytes)), 3U);
    EXPECT_FALSE(ring.write_all(view(bytes)));
    EXPECT_EQ(ring.free_space(), 1U);
    std::array<std::uint8_t, 2> out{};
    EXPECT_EQ(ring.peek(Span<std::uint8_t>(out), 1U), 2U);
    EXPECT_EQ(out.at(0), 2U);
    EXPECT_EQ(ring.consume(1U), 1U);
    EXPECT_EQ(ring.size(), 2U);
    ring.reset();
    EXPECT_TRUE(ring.empty());
}

TEST(RingBuffer, NoHeapAllocation) {
    ::testing::Test::RecordProperty("requirements", "RTR-static_vector-005");
    const wireforge::testing::AllocationTrap trap;
    StaticSpscByteRing<32> spsc;
    std::array<std::uint8_t, 32> storage{};
    LockedByteRing<> locked{Span<std::uint8_t>(storage)};
    const std::array<std::uint8_t, 8> bytes{};
    EXPECT_EQ(spsc.write(view(bytes)), 8U);
    EXPECT_TRUE(locked.write_all(view(bytes)));
    std::array<std::uint8_t, 8> out{};
    EXPECT_EQ(spsc.read(Span<std::uint8_t>(out)), 8U);
    EXPECT_EQ(locked.read(Span<std::uint8_t>(out)), 8U);
    EXPECT_EQ(trap.allocations(), 0U);
}

} // namespace
