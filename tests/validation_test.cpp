#include "wireforge/static_vector.hpp"
#include "wireforge/status.hpp"
#include "wireforge/validation.hpp"

#include <array>
#include <cstdint>
#include <gtest/gtest.h>
#include <limits>
#include <string_view>

namespace {

using wireforge::Status;

static_assert(wireforge::less(-1, 0U), "signed below unsigned");
static_assert(!wireforge::less(0U, -1), "unsigned never below a negative value");
static_assert(wireforge::less(std::uint64_t{1}, std::int64_t{2}), "mixed widths");
static_assert(!wireforge::less(std::numeric_limits<std::uint64_t>::max(), std::int64_t{-1}),
              "large unsigned value");
static_assert(wireforge::less(1.5, 2), "floating point");

TEST(Validation, RangeChecks) {
    EXPECT_EQ(wireforge::check_range(std::uint8_t{5}, 1U, 10U), Status::Ok);
    EXPECT_EQ(wireforge::check_range(std::uint8_t{0}, 1U, 10U), Status::OutOfRange);
    EXPECT_EQ(wireforge::check_range(std::int16_t{-5}, -4, 4), Status::OutOfRange);
    EXPECT_EQ(wireforge::check_min(std::uint32_t{0}, -3), Status::Ok);
    EXPECT_EQ(wireforge::check_max(std::int8_t{-1}, 0U), Status::Ok);
    EXPECT_EQ(wireforge::check_max(std::uint64_t{1} << 63U, std::int64_t{5}), Status::OutOfRange);
    EXPECT_EQ(wireforge::check_range(0.5F, 0.0, 1.0), Status::Ok);
    EXPECT_EQ(wireforge::check_range(1.5, 0.0, 1.0), Status::OutOfRange);
    EXPECT_EQ(wireforge::check_range(std::numeric_limits<double>::quiet_NaN(), 0.0, 1.0),
              Status::OutOfRange);
}

TEST(Validation, BitWidthChecks) {
    EXPECT_EQ(wireforge::check_bits(std::uint8_t{7}, 3U), Status::Ok);
    EXPECT_EQ(wireforge::check_bits(std::uint8_t{8}, 3U), Status::OutOfRange);
    EXPECT_EQ(wireforge::check_bits(std::int8_t{-16}, 5U), Status::Ok);
    EXPECT_EQ(wireforge::check_bits(std::int8_t{15}, 5U), Status::Ok);
    EXPECT_EQ(wireforge::check_bits(std::int8_t{16}, 5U), Status::OutOfRange);
    EXPECT_EQ(wireforge::check_bits(std::int8_t{-17}, 5U), Status::OutOfRange);
    EXPECT_EQ(wireforge::check_bits(std::int64_t{-1}, 63U), Status::Ok);
    EXPECT_EQ(wireforge::check_bits(std::numeric_limits<std::int64_t>::min(), 63U),
              Status::OutOfRange);
    EXPECT_EQ(wireforge::check_bits(std::uint64_t{1} << 62U, 63U), Status::Ok);
}

TEST(Validation, CountsAndCombination) {
    EXPECT_EQ(wireforge::check_count(2U, 1U, 3U), Status::Ok);
    EXPECT_EQ(wireforge::check_count(0U, 1U, 3U), Status::CountExceedsMax);
    EXPECT_EQ(wireforge::check_count_equals(4U, 4), Status::Ok);
    EXPECT_EQ(wireforge::check_count_equals(4U, -1), Status::CountExceedsMax);
    EXPECT_EQ(wireforge::check_true(false), Status::OutOfRange);
    EXPECT_EQ(wireforge::first_error(Status::Ok, Status::Truncated, Status::OutOfRange),
              Status::Truncated);
    EXPECT_EQ(wireforge::first_error(Status::Ok), Status::Ok);
    EXPECT_EQ(wireforge::status_if(true, Status::InvalidLength), Status::Ok);
    EXPECT_EQ(wireforge::status_if(false, Status::InvalidLength), Status::InvalidLength);
    EXPECT_EQ(wireforge::size_if_ok(Status::Ok, 12U), 12U);
    EXPECT_EQ(wireforge::size_if_ok(Status::Truncated, 12U), 0U);
}

struct Item {
    int value = 0;
};

[[nodiscard]] Status validate(const Item& item) {
    return item.value >= 0 ? Status::Ok : Status::OutOfRange;
}

TEST(Validation, EachElement) {
    std::array<Item, 3> items{};
    EXPECT_EQ(wireforge::validate_each(items), Status::Ok);
    items.at(1).value = -1;
    EXPECT_EQ(wireforge::validate_each(items), Status::OutOfRange);
    const std::array<int, 3> numbers{1, 2, 30};
    const auto small = [](int number) { return wireforge::check_max(number, 10); };
    EXPECT_EQ(wireforge::check_each(numbers, small), Status::OutOfRange);
}

TEST(Containers, ListAndTextConstructors) {
    const wireforge::StaticVector<int, 3> values{1, 2, 3, 4};
    EXPECT_EQ(values.size(), 3U) << "values beyond the capacity are dropped";
    EXPECT_EQ(values[2], 3);
    const wireforge::FixedString<4> text{std::string_view("hello")};
    EXPECT_EQ(text.view(), std::string_view("hell"));
    constexpr std::array<std::uint16_t, 3> filled = wireforge::filled<std::uint16_t, 3>(7U);
    EXPECT_EQ(filled.at(2), 7U);
}

} // namespace
