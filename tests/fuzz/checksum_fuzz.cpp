// libFuzzer target: table and bitwise CRC engines agree, and splitting the input does not change
// the result.
#include "wireforge/checksum.hpp"
#include "wireforge/span.hpp"

#include <cstddef>
#include <cstdint>
#include <cstdlib>

namespace {

using wireforge::Span;

template <unsigned W, std::uint64_t P, std::uint64_t I, bool RI, bool RO, std::uint64_t X>
void check(Span<const std::uint8_t> data, std::size_t split) noexcept {
    using Table = wireforge::Crc<W, P, I, RI, RO, X, true>;
    using Bitwise = wireforge::Crc<W, P, I, RI, RO, X, false>;
    const std::uint64_t whole = Table::compute(data);
    typename Bitwise::State state = Bitwise::init();
    Bitwise::update(state, data.first(split));
    Bitwise::update(state, data.subspan(split));
    if (whole != Bitwise::finalize(state) || whole != Bitwise::compute(data)) {
        std::abort();
    }
}

} // namespace

/// @brief libFuzzer entry point.
extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
    const Span<const std::uint8_t> input(data, size);
    const std::size_t split = input.empty() ? 0U : input[0] % (input.size() + 1U);
    check<32U, 0x04C11DB7U, 0xFFFFFFFFU, true, true, 0xFFFFFFFFU>(input, split);
    check<16U, 0x1021U, 0xFFFFU, false, false, 0x0000U>(input, split);
    check<16U, 0x8005U, 0x0000U, true, true, 0x0000U>(input, split);
    check<8U, 0x07U, 0x00U, false, false, 0x00U>(input, split);
    check<5U, 0x05U, 0x1FU, true, true, 0x1FU>(input, split);
    check<64U, 0x42F0E1EBA9EA3693U, 0xFFFFFFFFFFFFFFFFU, true, true, 0xFFFFFFFFFFFFFFFFU>(input,
                                                                                          split);
    return 0;
}
