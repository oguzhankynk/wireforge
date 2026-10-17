#include "support/alloc_trap.hpp"
#include "wireforge/byte_io.hpp"
#include "wireforge/checksum.hpp"
#include "wireforge/dispatcher.hpp"
#include "wireforge/frame_decoder.hpp"
#include "wireforge/link.hpp"
#include "wireforge/span.hpp"
#include "wireforge/status.hpp"
#include "wireforge/transport.hpp"

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <gtest/gtest.h>
#include <thread>

namespace {

using wireforge::ByteWriter;
using wireforge::ChecksumCoverage;
using wireforge::Crc32;
using wireforge::Endian;
using wireforge::FrameDecoder;
using wireforge::FrameFormat;
using wireforge::FramingKind;
using wireforge::HeaderLayout;
using wireforge::LengthCounts;
using wireforge::MessageHandler;
using wireforge::Span;
using wireforge::Status;
using Payload = Span<const std::uint8_t>;

constexpr std::size_t kMaxFrame = 32U;
constexpr std::uint64_t kSync = 0xA55AU;
constexpr std::uint16_t kStatusId = 0x0101U;
constexpr std::uint16_t kCommandId = 0x0201U;

// Header: sync u16, id u16, length u16; trailer: CRC-32 over header and payload.
constexpr FrameFormat kFormat{FramingKind::SyncLength,
                              Endian::Big,
                              6U,
                              4U,
                              kMaxFrame,
                              {0U, 2U},
                              kSync,
                              {4U, 2U},
                              LengthCounts::Payload,
                              0U,
                              ChecksumCoverage::HeaderAndPayload,
                              true};
constexpr HeaderLayout kLayout{6U, 4U, {2U, 2U}, Endian::Big};

// Encodes one frame with a 4-byte big-endian counter as payload.
class CounterEncoder {
public:
    CounterEncoder(std::uint16_t message_id, std::uint32_t value)
        : message_id_(message_id), value_(value) {}

    Status operator()(Span<std::uint8_t> buffer, std::size_t& size) const {
        ByteWriter writer(buffer);
        Status status = writer.write_unsigned(kSync, 2U, Endian::Big);
        status =
            status == Status::Ok ? writer.write_unsigned(message_id_, 2U, Endian::Big) : status;
        status = status == Status::Ok ? writer.write_unsigned(4U, 2U, Endian::Big) : status;
        status = status == Status::Ok ? writer.write_unsigned(value_, 4U, Endian::Big) : status;
        if (status != Status::Ok) {
            return status;
        }
        status = writer.write_unsigned(Crc32::compute(writer.written()), 4U, Endian::Big);
        size = writer.position();
        return status;
    }

private:
    std::uint16_t message_id_;
    std::uint32_t value_;
};

// Checks that values arrive in order and counts them.
class Receiver {
public:
    Status on_status(const wireforge::RawMessage& message) {
        const Payload payload = message.payload;
        std::uint64_t value = 0U;
        wireforge::ByteReader reader(payload);
        const Status status = reader.read_unsigned(value, 4U, Endian::Big);
        if (status != Status::Ok) {
            return status;
        }
        in_order_ = in_order_.load() && value == expected_;
        expected_ = value + 1U;
        ++count_;
        return Status::Ok;
    }
    Status on_command(const wireforge::RawMessage& /*message*/) {
        ++commands_;
        return Status::Ok;
    }
    [[nodiscard]] std::uint32_t count() const { return count_.load(); }
    [[nodiscard]] std::uint32_t commands() const { return commands_.load(); }
    [[nodiscard]] bool in_order() const { return in_order_.load(); }

private:
    std::atomic<std::uint32_t> count_{0U};
    std::atomic<std::uint32_t> commands_{0U};
    std::atomic<bool> in_order_{true};
    std::uint64_t expected_ = 0U;
};

template <typename Transport>
using TestLink =
    wireforge::Link<Transport, FrameDecoder<Crc32>, wireforge::Dispatcher<4>, kMaxFrame>;

class LinkTest : public ::testing::Test {
protected:
    void SetUp() override {
        ASSERT_EQ(dispatcher_.add(kStatusId, MessageHandler::bind<&Receiver::on_status>(receiver_)),
                  Status::Ok);
        ASSERT_EQ(
            dispatcher_.add(kCommandId, MessageHandler::bind<&Receiver::on_command>(receiver_)),
            Status::Ok);
        ASSERT_EQ(dispatcher_.start(), Status::Ok);
    }

    [[nodiscard]] Receiver& receiver() { return receiver_; }
    [[nodiscard]] FrameDecoder<Crc32>& decoder() { return decoder_; }
    [[nodiscard]] const wireforge::Dispatcher<4>& dispatcher() const { return dispatcher_; }

private:
    Receiver receiver_;
    FrameDecoder<Crc32> decoder_{kFormat};
    wireforge::Dispatcher<4> dispatcher_{kLayout};
};

// Sends status frames with values 0 .. count-1; stops at the first error.
template <typename Link> Status send_counters(Link& link, std::uint32_t count) {
    for (std::uint32_t value = 0U; value < count; ++value) {
        const Status status = link.send(CounterEncoder(kStatusId, value));
        if (status != Status::Ok) {
            return status;
        }
    }
    return Status::Ok;
}

TEST_F(LinkTest, LoopbackSendAndPoll) {
    wireforge::LoopbackTransport<256> transport;
    TestLink<wireforge::LoopbackTransport<256>> link(transport, decoder(), dispatcher());
    ASSERT_EQ(send_counters(link, 5U), Status::Ok);
    ASSERT_EQ(link.send(CounterEncoder(kCommandId, 0U)), Status::Ok);
    const wireforge::PollResult result = link.poll();
    EXPECT_EQ(result.frames, 6U);
    EXPECT_EQ(result.errors, 0U);
}

TEST_F(LinkTest, LoopbackDeliversInOrderAndCounts) {
    wireforge::LoopbackTransport<256> transport;
    TestLink<wireforge::LoopbackTransport<256>> link(transport, decoder(), dispatcher());
    ASSERT_EQ(send_counters(link, 5U), Status::Ok);
    ASSERT_EQ(link.send(CounterEncoder(kCommandId, 0U)), Status::Ok);
    static_cast<void>(link.poll());
    EXPECT_EQ(receiver().count(), 5U);
    EXPECT_EQ(receiver().commands(), 1U);
    EXPECT_TRUE(receiver().in_order());
    EXPECT_EQ(link.counters().tx_frames.load(), 6U);
    EXPECT_EQ(link.counters().rx_bytes.load(), link.counters().tx_bytes.load());
    EXPECT_EQ(link.poll().frames, 0U);
}

TEST_F(LinkTest, GarbageAndUnknownIdsAreReported) {
    wireforge::LoopbackTransport<256> transport;
    TestLink<wireforge::LoopbackTransport<256>> link(transport, decoder(), dispatcher());
    const std::array<std::uint8_t, 3> garbage{0x00U, 0xA5U, 0x11U};
    ASSERT_EQ(link.send_frame(Payload(garbage)), Status::Ok);
    ASSERT_EQ(link.send(CounterEncoder(0x0777U, 0U)), Status::Ok);
    ASSERT_EQ(link.send(CounterEncoder(kStatusId, 0U)), Status::Ok);
    const wireforge::PollResult result = link.poll();
    EXPECT_EQ(result.frames, 1U);
    EXPECT_EQ(result.errors, 1U);
    EXPECT_EQ(result.last_error, Status::UnknownMessageId);
    EXPECT_EQ(decoder().counters().discarded_bytes.load(), 3U);
}

TEST_F(LinkTest, EncoderErrorAndFullTransport) {
    wireforge::LoopbackTransport<20> transport; // holds one frame (18 bytes) only
    TestLink<wireforge::LoopbackTransport<20>> link(transport, decoder(), dispatcher());
    const auto failing = [](Span<std::uint8_t> /*buffer*/, std::size_t& /*size*/) {
        return Status::OutOfRange;
    };
    EXPECT_EQ(link.send(failing), Status::OutOfRange);
    const auto oversized = [](Span<std::uint8_t> /*buffer*/, std::size_t& size) {
        size = kMaxFrame + 1U;
        return Status::Ok;
    };
    EXPECT_EQ(link.send(oversized), Status::BufferTooSmall);
    ASSERT_EQ(link.send(CounterEncoder(kStatusId, 0U)), Status::Ok);
    EXPECT_EQ(link.send(CounterEncoder(kStatusId, 1U)), Status::BufferTooSmall);
    EXPECT_EQ(link.counters().tx_errors.load(), 3U);
    EXPECT_EQ(link.poll().frames, 1U) << "the cut second frame is skipped by resync";
}

// Adapter from a driver with its own API to the transport requirements, through ITransport.
class DriverAdapter final : public wireforge::ITransport {
public:
    std::size_t send(Span<const std::uint8_t> bytes) noexcept override { return wire_.send(bytes); }
    std::size_t receive(Span<std::uint8_t> buffer) noexcept override {
        return wire_.receive(buffer);
    }

private:
    wireforge::LoopbackTransport<128> wire_;
};

TEST_F(LinkTest, WorksThroughITransport) {
    DriverAdapter adapter;
    wireforge::ITransport& transport = adapter;
    TestLink<wireforge::ITransport> link(transport, decoder(), dispatcher());
    ASSERT_EQ(link.send(CounterEncoder(kStatusId, 0U)), Status::Ok);
    EXPECT_EQ(link.poll().frames, 1U);
}

class SizeRecorder {
public:
    Status operator()(const wireforge::RawMessage& message) {
        const Payload payload = message.payload;
        last_size_ = payload.size();
        return Status::Ok;
    }
    [[nodiscard]] std::size_t last_size() const { return last_size_; }

private:
    std::size_t last_size_ = 0U;
};

TEST(UnframedLink, EachReadIsOneFrame) {
    SizeRecorder recorder;
    wireforge::RawDispatcher<> dispatcher(2U);
    ASSERT_EQ(dispatcher.set_handler(MessageHandler::from_callable(recorder)), Status::Ok);
    ASSERT_EQ(dispatcher.start(), Status::Ok);
    wireforge::LoopbackTransport<64> transport;
    wireforge::Unframed unframed;
    wireforge::Link<wireforge::LoopbackTransport<64>, wireforge::Unframed,
                    wireforge::RawDispatcher<>, 16U>
        link(transport, unframed, dispatcher);
    const std::array<std::uint8_t, 7> frame{1U, 2U, 3U, 4U, 5U, 0xCCU, 0xCCU};
    ASSERT_EQ(link.send_frame(Payload(frame)), Status::Ok);
    const wireforge::PollResult result = link.poll();
    EXPECT_EQ(result.frames, 1U);
    EXPECT_EQ(recorder.last_size(), 5U) << "the 2-byte trailer is not part of the payload";
    EXPECT_EQ(link.poll().frames, 0U);
    EXPECT_EQ(dispatcher.dispatch_frame(Payload(frame).first(1U)), Status::Truncated);
}

TEST(UnframedLink, OversizedReadIsRejected) {
    SizeRecorder recorder;
    wireforge::RawDispatcher<> dispatcher(0U);
    ASSERT_EQ(dispatcher.set_handler(MessageHandler::from_callable(recorder)), Status::Ok);
    ASSERT_EQ(dispatcher.start(), Status::Ok);
    wireforge::LoopbackTransport<128> transport;
    wireforge::Unframed unframed;
    wireforge::Link<wireforge::LoopbackTransport<128>, wireforge::Unframed,
                    wireforge::RawDispatcher<>, 16U>
        link(transport, unframed, dispatcher);
    std::array<std::uint8_t, 90> oversized{};
    ASSERT_EQ(link.send_frame(Payload(oversized)), Status::Ok);
    const wireforge::PollResult rejected = link.poll();
    EXPECT_EQ(rejected.frames, 0U);
    EXPECT_EQ(rejected.errors, 1U);
    EXPECT_EQ(rejected.last_error, Status::InvalidLength);
    EXPECT_EQ(transport.pending(), 0U) << "the whole read is dropped";
    EXPECT_EQ(link.counters().rx_bytes.load(), 90U);
    ASSERT_EQ(link.send_frame(Payload(oversized).first(16U)), Status::Ok);
    EXPECT_EQ(link.poll().frames, 1U) << "a read of exactly MaxFrame bytes is one frame";
    EXPECT_EQ(recorder.last_size(), 16U);
}

constexpr std::uint32_t kThreadedFrames = 3000U;
constexpr std::size_t kFrameBytes = 18U;

template <typename Link, typename Transport>
void send_with_flow_control(Link& link, Transport& transport) {
    for (std::uint32_t value = 0U; value < kThreadedFrames; ++value) {
        while (transport.free_space() < kFrameBytes) {
            std::this_thread::yield();
        }
        if (link.send(CounterEncoder(kStatusId, value)) != Status::Ok) {
            return;
        }
    }
}

TEST_F(LinkTest, SenderAndPollerOnTwoThreads) {
    using Transport = wireforge::LoopbackTransport<128>;
    Transport transport;
    TestLink<Transport> link(transport, decoder(), dispatcher());
    std::thread sender(send_with_flow_control<TestLink<Transport>, Transport>, std::ref(link),
                       std::ref(transport));
    std::size_t errors = 0U;
    while (receiver().count() < kThreadedFrames) {
        errors += link.poll().errors;
        std::this_thread::yield();
    }
    sender.join();
    EXPECT_EQ(errors, 0U);
    EXPECT_TRUE(receiver().in_order());
    EXPECT_EQ(link.counters().tx_frames.load(), kThreadedFrames);
}

TEST_F(LinkTest, NoHeapAllocation) {
    wireforge::LoopbackTransport<256> transport;
    TestLink<wireforge::LoopbackTransport<256>> link(transport, decoder(), dispatcher());
    const wireforge::testing::AllocationTrap trap;
    ASSERT_EQ(link.send(CounterEncoder(kStatusId, 0U)), Status::Ok);
    EXPECT_EQ(link.poll().frames, 1U);
    EXPECT_EQ(trap.allocations(), 0U);
}

} // namespace
