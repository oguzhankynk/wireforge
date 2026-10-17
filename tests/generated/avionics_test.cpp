// Built twice: with the platformA and with the platformB profile definitions.
#include "avionics_demo/model.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <gtest/gtest.h>
#include <type_traits>

#if !defined(EXPECTED_TRACKS)
#error "EXPECTED_TRACKS must be defined by the build"
#endif

namespace {

using demo::avionics::Command;
using demo::avionics::NavSolution;
using demo::avionics::NodeBStatus;
using wireforge::Status;
using wireforge::type_tag;

static_assert(demo::avionics::defines::kMAX_TRACKS == EXPECTED_TRACKS, "profile define applies");
static_assert(decltype(NavSolution::tracks)::capacity() == EXPECTED_TRACKS,
              "capacity follows the profile");
static_assert(NodeBStatus{}.channelMv.size() == EXPECTED_TRACKS, "fixed array follows CHANNELS");
static_assert(min_wire_size(type_tag<NodeBStatus>) == 4U + (std::size_t{2U} * EXPECTED_TRACKS),
              "fill may be empty");

#if defined(PLATFORM_B)
// flags 1, hasAlt 1, lat 8, lon 8, trackCount 1, extra 1, ratio 4
static_assert(min_wire_size(type_tag<NavSolution>) == 24U, "platformB has the extra field");
#else
static_assert(min_wire_size(type_tag<NavSolution>) == 23U, "platformA has no extra field");
#endif
static_assert(max_wire_size(type_tag<NavSolution>) ==
                  min_wire_size(type_tag<NavSolution>) + 2U + (std::size_t{3U} * EXPECTED_TRACKS),
              "optional alt and full track list");
static_assert(min_wire_size(type_tag<Command>) == 5U, "checksum is wire only");

TEST(GeneratedAvionics, TrackCountIsLimitedByTheProfile) {
    NavSolution solution{};
    for (int index = 0; index < EXPECTED_TRACKS; ++index) {
        ASSERT_EQ(solution.tracks.push_back({}), Status::Ok);
    }
    EXPECT_EQ(solution.tracks.push_back({}), Status::CountExceedsMax);
    EXPECT_EQ(validate(solution), Status::Ok);
    solution.tracks[0].quality = 101U;
    EXPECT_EQ(validate(solution), Status::OutOfRange);
}

TEST(GeneratedAvionics, BitGroupMembersAndRanges) {
    NavSolution solution{};
    solution.flags.mode = 5U;
    EXPECT_EQ(validate(solution), Status::Ok);
    solution.flags.mode = 6U;
    EXPECT_EQ(validate(solution), Status::OutOfRange);
    solution.flags.mode = 0U;
    ASSERT_EQ(solution.alt.push_back(-501), Status::Ok);
    EXPECT_EQ(validate(solution), Status::OutOfRange);
}

TEST(GeneratedAvionics, ConditionalFieldExistsOnlyOnPlatformB) {
    NavSolution solution{};
    solution.flags.valid = true;
#if defined(PLATFORM_B)
    solution.extra = 7U;
    EXPECT_EQ(solution.extra, 7U);
#endif
    EXPECT_EQ(validate(solution), Status::Ok);
    Command command{};
    EXPECT_EQ(command.target, 1U);
    command.target = 5U;
    EXPECT_EQ(validate(command), Status::OutOfRange);
}

} // namespace

namespace {

TEST(GeneratedAvionicsCodec, CustomChecksumIsVerified) {
    Command command{};
    command.opcode = 0x0102U;
    std::array<std::uint8_t, 8> buffer{};
    std::size_t size = 0U;
    ASSERT_EQ(serialize(command, wireforge::Span<std::uint8_t>(buffer), size), Status::Ok);
    ASSERT_EQ(size, 5U);
    EXPECT_EQ(buffer.at(4), 0x04U) << "sum of 01 01 02";
    Command decoded{};
    EXPECT_EQ(deserialize(wireforge::Span<const std::uint8_t>(buffer.data(), size), decoded),
              Status::Ok);
    EXPECT_EQ(decoded.opcode, 0x0102U);
    buffer.at(1) ^= 0x01U;
    EXPECT_EQ(deserialize(wireforge::Span<const std::uint8_t>(buffer.data(), size), decoded),
              Status::ChecksumMismatch);
}

TEST(GeneratedAvionicsCodec, FillArrayTakesTheRest) {
    NodeBStatus status{};
    ASSERT_EQ(status.diag.resize(3U), Status::Ok);
    std::array<std::uint8_t, 64> buffer{};
    std::size_t size = 0U;
    ASSERT_EQ(serialize(status, wireforge::Span<std::uint8_t>(buffer), size), Status::Ok);
    EXPECT_EQ(size, 4U + (std::size_t{2U} * EXPECTED_TRACKS) + 3U);
    NodeBStatus decoded{};
    ASSERT_EQ(deserialize(wireforge::Span<const std::uint8_t>(buffer.data(), size), decoded),
              Status::Ok);
    EXPECT_EQ(decoded.diag.size(), 3U);
}

} // namespace

#include "avionics_demo/interfaces/nodeB_to_our.hpp"
#include "avionics_demo/interfaces/our_to_nodeA.hpp"
#include "avionics_demo/interfaces/our_to_nodeC.hpp"

namespace {

namespace node_a = demo::avionics::our_to_nodeA;

// The version namespace is inline: both spellings name the same entities.
static_assert(node_a::v1_2::kVersionMajor == 1U && node_a::v1_2::kVersionMinor == 2U,
              "interface version");
static_assert(std::is_same_v<node_a::Handler, node_a::v1_2::Handler>, "inline namespace");
static_assert(node_a::kNavSolutionId == 0x0201U, "message id");
static_assert(demo::avionics::our_to_nodeC::kCommandId == 0x30U, "per-interface id override");
static_assert(node_a::kNavSolutionEncoding.bit_order == wireforge::BitOrder::LsbFirst,
              "message bit order");
static_assert(demo::avionics::nodeB_to_our::kNodeBStatusEncoding.endian ==
                  wireforge::Endian::Little,
              "interface byte order");
static_assert(node_a::kMaxFrameSize == (EXPECTED_TRACKS == 3 ? 128U : 192U),
              "max_frame_size follows the application constant per profile");

class Counter final : public demo::avionics::our_to_nodeC::Handler {
public:
    void onCommandReceived(const Command& message) noexcept override {
        last_ = message.opcode;
        ++count_;
    }
    [[nodiscard]] std::uint16_t last() const { return last_; }
    [[nodiscard]] int count() const { return count_; }

private:
    std::uint16_t last_ = 0U;
    int count_ = 0;
};

TEST(GeneratedAvionicsInterfaces, CommandsLoopBackWithSequenceNumbers) {
    namespace node_c = demo::avionics::our_to_nodeC;
    Counter counter;
    node_c::Endpoint endpoint(counter);
    ASSERT_EQ(endpoint.status(), Status::Ok);
    wireforge::LoopbackTransport<256> transport;
    node_c::Link<wireforge::LoopbackTransport<256>> link(transport, endpoint.decoder(),
                                                         endpoint.dispatcher());
    for (std::uint16_t opcode = 1U; opcode <= 5U; ++opcode) {
        Command command{};
        command.opcode = opcode;
        node_c::Header header{};
        header.seq = static_cast<std::uint8_t>(opcode);
        ASSERT_EQ(link.send([&](wireforge::Span<std::uint8_t> buffer, std::size_t& size) {
            return node_c::encode_frame(command, buffer, size, header);
        }),
                  Status::Ok);
    }
    const wireforge::PollResult result = link.poll();
    EXPECT_EQ(result.frames, 5U);
    EXPECT_EQ(counter.count(), 5);
    EXPECT_EQ(counter.last(), 5U);
}

TEST(GeneratedAvionicsInterfaces, HeaderDecodingChecksConstants) {
    std::array<std::uint8_t, 64> buffer{};
    std::size_t size = 0U;
    ASSERT_EQ(node_a::encode_frame(NavSolution{}, wireforge::Span<std::uint8_t>(buffer), size),
              Status::Ok);
    const node_a::Header header{};
    EXPECT_EQ(
        node_a::decode_header(wireforge::Span<const std::uint8_t>(buffer.data(), size), header),
        Status::Ok);
    buffer.at(2) = 2U; // protocol version
    EXPECT_EQ(
        node_a::decode_header(wireforge::Span<const std::uint8_t>(buffer.data(), size), header),
        Status::ConstMismatch);
    EXPECT_EQ(node_a::decode_header(wireforge::Span<const std::uint8_t>(buffer.data(), 3U), header),
              Status::Truncated);
}

// Keeps the raw messages; decodes them only when asked to forward them to the default hook.
class RawCommands final : public demo::avionics::our_to_nodeC::Handler {
public:
    explicit RawCommands(bool forward) : forward_(forward) {}
    wireforge::Status onCommandRaw(const wireforge::RawMessage& raw) noexcept override {
        ++raw_;
        size_ = raw.payload.size();
        return forward_ ? demo::avionics::our_to_nodeC::Handler::onCommandRaw(raw) : Status::Ok;
    }
    void onCommandReceived(const Command& /*message*/) noexcept override { ++decoded_; }
    [[nodiscard]] std::size_t raw() const { return raw_; }
    [[nodiscard]] std::size_t decoded() const { return decoded_; }
    [[nodiscard]] std::size_t size() const { return size_; }

private:
    bool forward_;
    std::size_t raw_ = 0U;
    std::size_t decoded_ = 0U;
    std::size_t size_ = 0U;
};

// Sends one frame through a loopback link into @p handler and polls it once.
wireforge::PollResult loop_once(demo::avionics::our_to_nodeC::Handler& handler,
                                wireforge::Span<const std::uint8_t> frame) {
    namespace node_c = demo::avionics::our_to_nodeC;
    node_c::Endpoint endpoint(handler);
    EXPECT_EQ(endpoint.status(), Status::Ok);
    wireforge::LoopbackTransport<64> transport;
    node_c::Link<wireforge::LoopbackTransport<64>> link(transport, endpoint.decoder(),
                                                        endpoint.dispatcher());
    EXPECT_EQ(link.send_frame(frame), Status::Ok);
    return link.poll();
}

TEST(GeneratedAvionicsInterfaces, RawHookSkipsPayloadChecks) {
    namespace node_c = demo::avionics::our_to_nodeC;
    Command command{};
    command.opcode = 0x0102U;
    std::array<std::uint8_t, 64> buffer{};
    std::size_t size = 0U;
    ASSERT_EQ(node_c::encode_frame(command, wireforge::Span<std::uint8_t>(buffer), size),
              Status::Ok);
    // The last byte is the payload checksum: the frame is intact, the payload is not.
    buffer.at(size - 1U) ^= 0xFFU;
    const wireforge::Span<const std::uint8_t> frame(buffer.data(), size);

    RawCommands raw_only(false);
    const wireforge::PollResult accepted = loop_once(raw_only, frame);
    EXPECT_EQ(accepted.frames, 1U);
    EXPECT_EQ(accepted.errors, 0U);
    EXPECT_EQ(raw_only.raw(), 1U);
    EXPECT_EQ(raw_only.decoded(), 0U);
    EXPECT_EQ(raw_only.size(), 5U) << "the payload without the frame header";

    RawCommands forwarding(true);
    const wireforge::PollResult rejected = loop_once(forwarding, frame);
    EXPECT_EQ(rejected.frames, 0U);
    EXPECT_EQ(rejected.errors, 1U);
    EXPECT_EQ(rejected.last_error, Status::ChecksumMismatch);
    EXPECT_EQ(forwarding.raw(), 1U);
    EXPECT_EQ(forwarding.decoded(), 0U);
}

} // namespace
