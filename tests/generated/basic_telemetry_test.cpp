#include "basic_telemetry/model.hpp"
#include "support/alloc_trap.hpp"

#include <gtest/gtest.h>

namespace {

using demo::telemetry::SensorReading;
using demo::telemetry::SensorState;
using demo::telemetry::Vec3;
using wireforge::Status;
using wireforge::type_tag;

// Header-free sizes: sensorId 1, state 1, temperature 4, accel 12, sampleCount 1, samples 0..4 x 2.
static_assert(min_wire_size(type_tag<SensorReading>) == 19U, "minimum size");
static_assert(max_wire_size(type_tag<SensorReading>) == 27U, "maximum size");
static_assert(max_wire_size(type_tag<Vec3>) == 12U, "nested size");
static_assert(validate(SensorReading{}) == Status::Ok, "defaults are valid at compile time");

TEST(GeneratedBasicTelemetry, DefaultsComeFromTheModel) {
    const SensorReading reading{};
    EXPECT_EQ(reading.sensorId, 1U);
    EXPECT_EQ(reading.state, SensorState::ready);
    EXPECT_EQ(reading.temperature, 20.0F);
    EXPECT_EQ(reading.accel.x, 0.0F);
    EXPECT_TRUE(reading.samples.empty());
    EXPECT_EQ(validate(reading), Status::Ok);
}

TEST(GeneratedBasicTelemetry, RangesAndEnumsAreValidated) {
    SensorReading reading{};
    reading.sensorId = 0U;
    EXPECT_EQ(validate(reading), Status::OutOfRange);
    reading.sensorId = 32U;
    EXPECT_EQ(validate(reading), Status::Ok);
    reading.temperature = 150.5F;
    EXPECT_EQ(validate(reading), Status::OutOfRange);
    reading.temperature = -80.0F;
    reading.state = static_cast<SensorState>(9U);
    EXPECT_EQ(validate(reading), Status::OutOfRange);
}

TEST(GeneratedBasicTelemetry, ArraysUseStaticStorage) {
    const wireforge::testing::AllocationTrap trap;
    SensorReading reading{};
    for (std::uint16_t sample = 0U; sample < 4U; ++sample) {
        ASSERT_EQ(reading.samples.push_back(sample), Status::Ok);
    }
    EXPECT_EQ(reading.samples.push_back(5U), Status::CountExceedsMax);
    EXPECT_EQ(validate(reading), Status::Ok);
    EXPECT_EQ(trap.allocations(), 0U);
}

} // namespace
