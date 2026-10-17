#include "support/alloc_trap.hpp"
#include "wireforge/byte_io.hpp"
#include "wireforge/delegate.hpp"
#include "wireforge/dispatcher.hpp"
#include "wireforge/span.hpp"
#include "wireforge/status.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <gtest/gtest.h>
#include <thread>

namespace {

using wireforge::Delegate;
using wireforge::Dispatcher;
using wireforge::Endian;
using wireforge::FallbackHandler;
using wireforge::HeaderLayout;
using wireforge::MessageHandler;
using wireforge::RawDispatcher;
using wireforge::Span;
using wireforge::Status;
using Payload = Span<const std::uint8_t>;

// Header: sync u16, id u16 (offset 2), length u8; trailer: 2 bytes.
constexpr HeaderLayout kLayout{5U, 2U, {2U, 2U}, Endian::Big};

using IntDelegate = Delegate<int(int)>;

int twice(int value) {
    return value * 2;
}

class Accumulator {
public:
    int add(int value) {
        total_ += value;
        return total_;
    }
    [[nodiscard]] int total() const { return total_; }

private:
    int total_ = 0;
};

TEST(Delegate, EmptyDelegate) {
    ::testing::Test::RecordProperty("requirements", "RTR-delegate-001");
    const IntDelegate empty;
    EXPECT_FALSE(empty);
    EXPECT_FALSE(IntDelegate::from_function(nullptr));
}

TEST(Delegate, FreeFunction) {
    ::testing::Test::RecordProperty("requirements", "RTR-delegate-002");
    const IntDelegate function = IntDelegate::from_function(&twice);
    ASSERT_TRUE(function);
    EXPECT_EQ(function(21), 42);
}

TEST(Delegate, MemberFunction) {
    ::testing::Test::RecordProperty("requirements", "RTR-delegate-002");
    Accumulator accumulator;
    const IntDelegate member = IntDelegate::bind<&Accumulator::add>(accumulator);
    EXPECT_EQ(member(5), 5);
    EXPECT_EQ(member(6), 11);
    EXPECT_EQ(accumulator.total(), 11);
}

TEST(Delegate, CallableIsReferencedNotCopied) {
    ::testing::Test::RecordProperty("requirements", "RTR-delegate-003");
    int offset = 100;
    auto lambda = [&offset](int value) { return value + offset; };
    const IntDelegate callable = IntDelegate::from_callable(lambda);
    EXPECT_EQ(callable(1), 101);
    offset = 200;
    EXPECT_EQ(callable(1), 201);
}

// Records which message reached which handler.
class Recorder {
public:
    Status on_status(const wireforge::RawMessage& message) {
        const Payload payload = message.payload;
        ++status_count_;
        last_size_ = payload.size();
        return Status::Ok;
    }
    Status on_command(const wireforge::RawMessage& message) {
        const Payload payload = message.payload;
        ++command_count_;
        last_size_ = payload.size();
        return payload.empty() ? Status::Truncated : Status::Ok;
    }
    [[nodiscard]] int status_count() const { return status_count_.load(); }
    [[nodiscard]] int command_count() const { return command_count_.load(); }
    [[nodiscard]] std::size_t last_size() const { return last_size_.load(); }

private:
    std::atomic<int> status_count_{0};
    std::atomic<int> command_count_{0};
    std::atomic<std::size_t> last_size_{0U};
};

template <std::size_t N> void register_both(Dispatcher<N>& dispatcher, Recorder& recorder) {
    ASSERT_EQ(dispatcher.add(0x0201U, MessageHandler::bind<&Recorder::on_command>(recorder)),
              Status::Ok);
    ASSERT_EQ(dispatcher.add(0x0101U, MessageHandler::bind<&Recorder::on_status>(recorder)),
              Status::Ok);
}

TEST(Dispatcher, DispatchesById) {
    ::testing::Test::RecordProperty("requirements", "RTR-dispatcher-001");
    Recorder recorder;
    Dispatcher<4> dispatcher(kLayout);
    register_both(dispatcher, recorder);
    EXPECT_EQ(dispatcher.size(), 2U);
    const std::array<std::uint8_t, 3> payload{1U, 2U, 3U};
    EXPECT_EQ(dispatcher.dispatch(0x0101U, Payload(payload)), Status::InvalidState)
        << "dispatching before start() is refused";
    ASSERT_EQ(dispatcher.start(), Status::Ok);
    EXPECT_TRUE(dispatcher.started());
    EXPECT_EQ(dispatcher.dispatch(0x0101U, Payload(payload)), Status::Ok);
    EXPECT_EQ(dispatcher.dispatch(0x0201U, Payload()), Status::Truncated) << "handler result";
    EXPECT_EQ(recorder.status_count(), 1);
    EXPECT_EQ(recorder.command_count(), 1);
    EXPECT_EQ(dispatcher.dispatch(0x0999U, Payload(payload)), Status::UnknownMessageId);
    EXPECT_EQ(dispatcher.counters().dispatched.load(), 2U);
    EXPECT_EQ(dispatcher.counters().unknown_ids.load(), 1U);
}

class HitCounter {
public:
    Status operator()(const wireforge::RawMessage& /*message*/) {
        ++hits_;
        return Status::Ok;
    }
    [[nodiscard]] int hits() const { return hits_; }

private:
    int hits_ = 0;
};

constexpr std::size_t kManyIds = 16U;

std::uint64_t scrambled_id(std::size_t index) {
    return (index * 37U) % 101U; // registered out of order
}

using ManyDispatcher = Dispatcher<kManyIds, wireforge::NullLock>;

Status register_scrambled(ManyDispatcher& dispatcher, std::array<HitCounter, kManyIds>& counters) {
    for (std::size_t index = 0U; index < kManyIds; ++index) {
        const Status status =
            dispatcher.add(scrambled_id(index), MessageHandler::from_callable(counters.at(index)));
        if (status != Status::Ok) {
            return status;
        }
    }
    return Status::Ok;
}

TEST(Dispatcher, ManyIdsAreFoundAfterSorting) {
    ::testing::Test::RecordProperty("requirements", "RTR-dispatcher-001");
    std::array<HitCounter, kManyIds> counters{};
    ManyDispatcher dispatcher(kLayout);
    ASSERT_EQ(register_scrambled(dispatcher, counters), Status::Ok);
    EXPECT_EQ(dispatcher.add(500U, MessageHandler::from_callable(counters.at(0))),
              Status::CountExceedsMax);
    ASSERT_EQ(dispatcher.start(), Status::Ok);
    for (std::size_t index = 0U; index < kManyIds; ++index) {
        EXPECT_EQ(dispatcher.dispatch(scrambled_id(index), Payload()), Status::Ok);
    }
    EXPECT_TRUE(std::all_of(counters.begin(), counters.end(),
                            [](const HitCounter& counter) { return counter.hits() == 1; }));
}

TEST(Dispatcher, RegistrationRules) {
    ::testing::Test::RecordProperty("requirements", "RTR-dispatcher-002");
    Recorder recorder;
    Dispatcher<2> dispatcher(kLayout);
    const MessageHandler handler = MessageHandler::bind<&Recorder::on_status>(recorder);
    EXPECT_EQ(dispatcher.add(1U, MessageHandler()), Status::InvalidState) << "empty handler";
    ASSERT_EQ(dispatcher.add(1U, handler), Status::Ok);
    EXPECT_EQ(dispatcher.add(1U, handler), Status::InvalidState) << "duplicate id";
    ASSERT_EQ(dispatcher.start(), Status::Ok);
    EXPECT_EQ(dispatcher.start(), Status::InvalidState);
    EXPECT_EQ(dispatcher.add(2U, handler), Status::InvalidState) << "registration after start";
    EXPECT_EQ(dispatcher.set_fallback(FallbackHandler()), Status::InvalidState);
}

TEST(Dispatcher, FallbackReceivesUnknownIds) {
    ::testing::Test::RecordProperty("requirements", "RTR-dispatcher-003");
    std::uint64_t seen = 0U;
    auto fallback = [&seen](std::uint64_t message_id, const wireforge::RawMessage& /*message*/) {
        seen = message_id;
        return Status::Ok;
    };
    Dispatcher<1> dispatcher(kLayout);
    ASSERT_EQ(dispatcher.set_fallback(FallbackHandler::from_callable(fallback)), Status::Ok);
    ASSERT_EQ(dispatcher.start(), Status::Ok);
    EXPECT_EQ(dispatcher.dispatch(77U, Payload()), Status::Ok);
    EXPECT_EQ(seen, 77U);
    EXPECT_EQ(dispatcher.counters().fallback.load(), 1U);
}

TEST(Dispatcher, DispatchFrameExtractsIdAndPayload) {
    ::testing::Test::RecordProperty("requirements", "RTR-dispatcher-004");
    Recorder recorder;
    Dispatcher<4> dispatcher(kLayout);
    register_both(dispatcher, recorder);
    ASSERT_EQ(dispatcher.start(), Status::Ok);
    // sync A5 5A, id 0x0201, length 3, payload 3 bytes, trailer 2 bytes
    const std::array<std::uint8_t, 10> frame{0xA5U, 0x5AU, 0x02U, 0x01U, 3U,
                                             9U,    9U,    9U,    0xEEU, 0xEEU};
    EXPECT_EQ(dispatcher.dispatch_frame(Payload(frame)), Status::Ok);
    EXPECT_EQ(recorder.command_count(), 1);
    EXPECT_EQ(recorder.last_size(), 3U);
    EXPECT_EQ(dispatcher.dispatch_frame(Payload(frame).first(6U)), Status::Truncated);
    Dispatcher<1> broken(HeaderLayout{2U, 0U, {1U, 2U}, Endian::Big});
    ASSERT_EQ(broken.start(), Status::Ok);
    EXPECT_EQ(broken.dispatch_frame(Payload(frame)), Status::InvalidState);
}

TEST(Dispatcher, ConcurrentDispatchAfterStart) {
    ::testing::Test::RecordProperty("requirements", "RTR-dispatcher-005");
    Recorder recorder;
    Dispatcher<4> dispatcher(kLayout);
    register_both(dispatcher, recorder);
    ASSERT_EQ(dispatcher.start(), Status::Ok);
    constexpr int kPerThread = 5000;
    const auto work = [&dispatcher](std::uint64_t message_id) {
        const std::array<std::uint8_t, 1> payload{1U};
        for (int index = 0; index < kPerThread; ++index) {
            static_cast<void>(dispatcher.dispatch(message_id, Payload(payload)));
        }
    };
    std::thread first(work, 0x0101U);
    std::thread second(work, 0x0201U);
    std::thread third(work, 0x0101U);
    std::thread fourth(work, 0x0300U);
    first.join();
    second.join();
    third.join();
    fourth.join();
    EXPECT_EQ(recorder.status_count(), 2 * kPerThread);
    EXPECT_EQ(recorder.command_count(), kPerThread);
    EXPECT_EQ(dispatcher.counters().unknown_ids.load(), static_cast<std::uint32_t>(kPerThread));
}

TEST(Dispatcher, ConcurrentRegistrationBeforeStart) {
    ::testing::Test::RecordProperty("requirements", "RTR-dispatcher-005");
    Recorder recorder;
    Dispatcher<64> dispatcher(kLayout);
    const MessageHandler handler = MessageHandler::bind<&Recorder::on_status>(recorder);
    const auto work = [&dispatcher, &handler](std::uint64_t base) {
        for (std::uint64_t index = 0U; index < 32U; ++index) {
            EXPECT_EQ(dispatcher.add(base + index, handler), Status::Ok);
        }
    };
    std::thread first(work, 0U);
    std::thread second(work, 1000U);
    first.join();
    second.join();
    ASSERT_EQ(dispatcher.start(), Status::Ok);
    EXPECT_EQ(dispatcher.size(), 64U);
    EXPECT_EQ(dispatcher.dispatch(1031U, Payload()), Status::Ok);
}

TEST(RawDispatcher, SingleHandler) {
    ::testing::Test::RecordProperty("requirements", "RTR-dispatcher-006");
    Recorder recorder;
    RawDispatcher<> dispatcher;
    EXPECT_EQ(dispatcher.set_handler(MessageHandler()), Status::InvalidState);
    EXPECT_EQ(dispatcher.dispatch(Payload()), Status::InvalidState);
    ASSERT_EQ(dispatcher.set_handler(MessageHandler::bind<&Recorder::on_status>(recorder)),
              Status::Ok);
    ASSERT_EQ(dispatcher.start(), Status::Ok);
    EXPECT_EQ(dispatcher.start(), Status::InvalidState);
    EXPECT_EQ(dispatcher.set_handler(MessageHandler::bind<&Recorder::on_command>(recorder)),
              Status::InvalidState);
    const std::array<std::uint8_t, 4> payload{};
    EXPECT_EQ(dispatcher.dispatch(Payload(payload)), Status::Ok);
    EXPECT_EQ(recorder.last_size(), 4U);
    EXPECT_EQ(dispatcher.counters().dispatched.load(), 1U);
}

TEST(RawDispatcher, WithoutHandlerReportsUnknown) {
    ::testing::Test::RecordProperty("requirements", "RTR-dispatcher-006");
    RawDispatcher<wireforge::NullLock> dispatcher;
    ASSERT_EQ(dispatcher.start(), Status::Ok);
    EXPECT_EQ(dispatcher.dispatch(Payload()), Status::UnknownMessageId);
    EXPECT_EQ(dispatcher.counters().unknown_ids.load(), 1U);
}

TEST(Dispatcher, NoHeapAllocation) {
    ::testing::Test::RecordProperty("requirements", "RTR-static_vector-005");
    Recorder recorder;
    const wireforge::testing::AllocationTrap trap;
    Dispatcher<4> dispatcher(kLayout);
    register_both(dispatcher, recorder);
    ASSERT_EQ(dispatcher.start(), Status::Ok);
    const std::array<std::uint8_t, 7> frame{0xA5U, 0x5AU, 0x01U, 0x01U, 0U, 0U, 0U};
    EXPECT_EQ(dispatcher.dispatch_frame(Payload(frame)), Status::Ok);
    EXPECT_EQ(trap.allocations(), 0U);
}

// Keeps the spans it was given, so they can be compared after the call.
class RawProbe {
public:
    Status operator()(const wireforge::RawMessage& message) {
        frame_ = message.frame;
        payload_ = message.payload;
        ++hits_;
        return Status::Ok;
    }
    [[nodiscard]] Payload frame() const { return frame_; }
    [[nodiscard]] Payload payload() const { return payload_; }
    [[nodiscard]] int hits() const { return hits_; }

private:
    Payload frame_;
    Payload payload_;
    int hits_ = 0;
};

TEST(Dispatcher, DispatchFramePassesFrameAndPayload) {
    ::testing::Test::RecordProperty("requirements", "RTR-dispatcher-004");
    RawProbe probe;
    Dispatcher<1> dispatcher(kLayout);
    ASSERT_EQ(dispatcher.add(0x0201U, MessageHandler::from_callable(probe)), Status::Ok);
    ASSERT_EQ(dispatcher.start(), Status::Ok);
    // sync A5 5A, id 0x0201, length 3, payload 3 bytes, trailer 2 bytes
    const std::array<std::uint8_t, 10> frame{0xA5U, 0x5AU, 0x02U, 0x01U, 3U,
                                             9U,    9U,    9U,    0xEEU, 0xEEU};
    EXPECT_EQ(dispatcher.dispatch_frame(Payload(frame)), Status::Ok);
    ASSERT_EQ(probe.hits(), 1);
    EXPECT_EQ(probe.frame().data(), Payload(frame).data());
    EXPECT_EQ(probe.frame().size(), 10U);
    EXPECT_EQ(probe.payload().data(), Payload(frame).subspan(5U).data());
    EXPECT_EQ(probe.payload().size(), 3U);
}

TEST(Dispatcher, DispatchPayloadUsesPayloadAsFrame) {
    ::testing::Test::RecordProperty("requirements", "RTR-dispatcher-004");
    RawProbe probe;
    Dispatcher<1> dispatcher(kLayout);
    ASSERT_EQ(dispatcher.add(7U, MessageHandler::from_callable(probe)), Status::Ok);
    ASSERT_EQ(dispatcher.start(), Status::Ok);
    const std::array<std::uint8_t, 2> payload{1U, 2U};
    EXPECT_EQ(dispatcher.dispatch(7U, Payload(payload)), Status::Ok);
    EXPECT_EQ(probe.frame().data(), probe.payload().data());
    EXPECT_EQ(probe.frame().size(), 2U);
}

TEST(Dispatcher, FallbackReceivesFrame) {
    ::testing::Test::RecordProperty("requirements", "RTR-dispatcher-003");
    std::size_t frame_size = 0U;
    auto fallback = [&frame_size](std::uint64_t /*message_id*/,
                                  const wireforge::RawMessage& message) {
        frame_size = message.frame.size();
        return Status::Ok;
    };
    Dispatcher<1> dispatcher(kLayout);
    ASSERT_EQ(dispatcher.set_fallback(FallbackHandler::from_callable(fallback)), Status::Ok);
    ASSERT_EQ(dispatcher.start(), Status::Ok);
    const std::array<std::uint8_t, 10> frame{0xA5U, 0x5AU, 0x02U, 0x01U, 3U,
                                             9U,    9U,    9U,    0xEEU, 0xEEU};
    EXPECT_EQ(dispatcher.dispatch_frame(Payload(frame)), Status::Ok);
    EXPECT_EQ(frame_size, 10U);
}

TEST(RawDispatcher, DispatchFramePassesFrame) {
    ::testing::Test::RecordProperty("requirements", "RTR-dispatcher-006");
    RawProbe probe;
    RawDispatcher<> dispatcher(2U, 1U);
    ASSERT_EQ(dispatcher.set_handler(MessageHandler::from_callable(probe)), Status::Ok);
    ASSERT_EQ(dispatcher.start(), Status::Ok);
    const std::array<std::uint8_t, 6> frame{1U, 2U, 3U, 4U, 0xCCU, 0xCCU};
    EXPECT_EQ(dispatcher.dispatch_frame(Payload(frame)), Status::Ok);
    ASSERT_EQ(probe.hits(), 1);
    EXPECT_EQ(probe.frame().data(), Payload(frame).data());
    EXPECT_EQ(probe.frame().size(), 6U);
    EXPECT_EQ(probe.payload().data(), Payload(frame).subspan(1U).data());
    EXPECT_EQ(probe.payload().size(), 3U);
}

} // namespace
