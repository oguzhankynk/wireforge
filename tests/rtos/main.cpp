// Test harness on FreeRTOS (Cortex-M4F, QEMU): the self-tests of the generated code of three
// examples, and the runtime used by several tasks with the lock of the kernel. The result is the
// exit code of the emulator: 0 when every check passed.
#include "FreeRTOS.h"
#include "freertos_lock.hpp"
#include "semihosting.hpp"
#include "task.h"
#include "wireforge/byte_io.hpp"
#include "wireforge/checksum.hpp"
#include "wireforge/dispatcher.hpp"
#include "wireforge/frame_decoder.hpp"
#include "wireforge/link.hpp"
#include "wireforge/ring_buffer.hpp"
#include "wireforge/span.hpp"
#include "wireforge/status.hpp"
#include "wireforge/transport.hpp"

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstdio>

// The generated self-tests, each with its main() renamed.
int selftest_basic_telemetry();
int selftest_avionics_platform_a();
int selftest_edge_cases();

namespace {

using wireforge::ByteWriter;
using wireforge::Crc32;
using wireforge::Endian;
using wireforge::FrameDecoder;
using wireforge::HeaderLayout;
using wireforge::MessageHandler;
using wireforge::Span;
using wireforge::Status;

static_assert(wireforge::is_lock_policy_v<FreeRtosLock>);

constexpr std::uint32_t kStackWords = 4096U;
constexpr UBaseType_t kTaskPriority = 2U;

int g_failures = 0;

void check(bool passed, const char* what) {
    if (!passed) {
        ++g_failures;
        std::printf("FAILED: %s\n", what);
    }
}

// --- the generated self-tests --------------------------------------------------------------

void run_selftests() {
    std::puts("-- generated self-tests");
    check(selftest_basic_telemetry() == 0, "self-test of basic_telemetry");
    check(selftest_avionics_platform_a() == 0, "self-test of avionics_demo (platformA)");
    check(selftest_edge_cases() == 0, "self-test of edge_cases");
}

// --- checksum ------------------------------------------------------------------------------

void run_checksum() {
    std::puts("-- checksum");
    const std::array<std::uint8_t, 9> text{'1', '2', '3', '4', '5', '6', '7', '8', '9'};
    check(Crc32::compute(Span<const std::uint8_t>(text)) == 0xCBF43926U, "CRC-32 check value");
}

// --- two tasks and a ring of the runtime -----------------------------------------------------

constexpr std::uint32_t kRecords = 2000U;

wireforge::LockedByteRing<>* g_ring = nullptr;
std::atomic<std::uint32_t> g_consumed{0U};
std::atomic<std::uint32_t> g_sum{0U};
std::atomic<std::uint32_t> g_corrupt{0U};
TaskHandle_t g_runner = nullptr;

void producer_task(void* /*argument*/) {
    for (std::uint32_t index = 0U; index < kRecords; ++index) {
        const std::array<std::uint8_t, 4> record{0xA5U, static_cast<std::uint8_t>(index >> 8U),
                                                 static_cast<std::uint8_t>(index & 0xFFU), 0x5AU};
        while (!g_ring->write_all(Span<const std::uint8_t>(record))) {
            vTaskDelay(1);
        }
    }
    xTaskNotifyGive(g_runner);
    vTaskDelete(nullptr);
}

void consumer_task(void* /*argument*/) {
    while (g_consumed.load() < kRecords) {
        std::array<std::uint8_t, 4> record{};
        if (g_ring->size() < record.size() || g_ring->read(Span<std::uint8_t>(record)) != 4U) {
            vTaskDelay(1);
            continue;
        }
        if (record.at(0) != 0xA5U || record.at(3) != 0x5AU) {
            ++g_corrupt;
        }
        g_sum += (std::uint32_t{record.at(1)} << 8U) | record.at(2);
        ++g_consumed;
    }
    xTaskNotifyGive(g_runner);
    vTaskDelete(nullptr);
}

void run_ring_between_tasks() {
    std::puts("-- ring between two tasks");
    static std::array<std::uint8_t, 64> storage{};
    static wireforge::LockedByteRing<> ring{Span<std::uint8_t>(storage)};
    g_ring = &ring;
    g_consumed = 0U;
    g_sum = 0U;
    g_corrupt = 0U;
    check(xTaskCreate(producer_task, "producer", kStackWords, nullptr, kTaskPriority, nullptr) ==
              pdPASS,
          "producer task created");
    check(xTaskCreate(consumer_task, "consumer", kStackWords, nullptr, kTaskPriority, nullptr) ==
              pdPASS,
          "consumer task created");
    ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    std::uint32_t expected = 0U;
    for (std::uint32_t index = 0U; index < kRecords; ++index) {
        expected += index;
    }
    check(g_corrupt.load() == 0U, "no record is corrupt");
    check(g_consumed.load() == kRecords, "every record arrives");
    check(g_sum.load() == expected, "every record arrives once");
}

// --- a link: one task sends frames, another polls and dispatches them -------------------------

constexpr std::size_t kMaxFrame = 32U;
constexpr std::uint64_t kSync = 0xA55AU;
constexpr std::uint16_t kStatusId = 0x0101U;
constexpr std::uint32_t kFrames = 400U;
constexpr wireforge::FrameFormat kFormat{wireforge::FramingKind::SyncLength,
                                         Endian::Big,
                                         6U,
                                         4U,
                                         kMaxFrame,
                                         {0U, 2U},
                                         kSync,
                                         {4U, 2U},
                                         wireforge::LengthCounts::Payload,
                                         0U,
                                         wireforge::ChecksumCoverage::HeaderAndPayload,
                                         true};
constexpr HeaderLayout kLayout{6U, 4U, {2U, 2U}, Endian::Big};

class CounterEncoder {
public:
    explicit CounterEncoder(std::uint32_t value) : value_(value) {}
    Status operator()(Span<std::uint8_t> buffer, std::size_t& size) const {
        ByteWriter writer(buffer);
        Status status = writer.write_unsigned(kSync, 2U, Endian::Big);
        status = status == Status::Ok ? writer.write_unsigned(kStatusId, 2U, Endian::Big) : status;
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
    std::uint32_t value_;
};

struct Receiver {
    Status on_status(const wireforge::RawMessage& message) {
        std::uint64_t value = 0U;
        wireforge::ByteReader reader(message.payload);
        const Status status = reader.read_unsigned(value, 4U, Endian::Big);
        in_order = in_order && value == expected;
        expected = static_cast<std::uint32_t>(value) + 1U;
        ++count;
        return status;
    }
    bool in_order = true;
    std::uint32_t expected = 0U;
    std::uint32_t count = 0U;
};

using Loopback = wireforge::LoopbackTransport<128>;
using TestLink =
    wireforge::Link<Loopback, FrameDecoder<Crc32>, wireforge::Dispatcher<4>, kMaxFrame>;

Loopback* g_transport = nullptr;
TestLink* g_link = nullptr;

void sender_task(void* /*argument*/) {
    for (std::uint32_t value = 0U; value < kFrames; ++value) {
        while (g_transport->free_space() < 18U) {
            vTaskDelay(1);
        }
        static_cast<void>(g_link->send(CounterEncoder(value)));
    }
    xTaskNotifyGive(g_runner);
    vTaskDelete(nullptr);
}

void run_link_between_tasks() {
    std::puts("-- link between two tasks");
    static Loopback transport;
    static FrameDecoder<Crc32> decoder(kFormat);
    static Receiver receiver;
    static wireforge::Dispatcher<4> dispatcher(kLayout);
    check(dispatcher.add(kStatusId, MessageHandler::bind<&Receiver::on_status>(receiver)) ==
              Status::Ok,
          "handler registered");
    check(dispatcher.start() == Status::Ok, "dispatcher started");
    static TestLink link(transport, decoder, dispatcher);
    g_transport = &transport;
    g_link = &link;
    check(xTaskCreate(sender_task, "sender", kStackWords, nullptr, kTaskPriority, nullptr) ==
              pdPASS,
          "sender task created");
    bool sent = false;
    while (receiver.count < kFrames) {
        static_cast<void>(link.poll());
        if (!sent) {
            sent = ulTaskNotifyTake(pdTRUE, 0U) != 0U;
        }
        vTaskDelay(1);
    }
    check(receiver.in_order, "frames arrive in order");
    check(link.counters().tx_errors.load() == 0U, "no transmit error");
    check(decoder.counters().frames.load() == kFrames, "every frame is delivered");
}

// --- the runner -------------------------------------------------------------------------------

void runner_task(void* /*argument*/) {
    run_checksum();
    run_selftests();
    run_ring_between_tasks();
    run_link_between_tasks();
    std::printf("rtos harness: %d failure(s)\n", g_failures);
    std::fflush(nullptr);
    rtos::exit_with(g_failures == 0 ? 0U : 1U);
}

} // namespace

extern "C" {

// FreeRTOS asks for the memory of the idle task when static allocation is on.
void vApplicationGetIdleTaskMemory(StaticTask_t** task, StackType_t** stack,
                                   configSTACK_DEPTH_TYPE* size) {
    static StaticTask_t idle_task;
    static StackType_t idle_stack[configMINIMAL_STACK_SIZE];
    *task = &idle_task;
    *stack = idle_stack;
    *size = configMINIMAL_STACK_SIZE;
}

int main(void) {
    g_runner = nullptr;
    if (xTaskCreate(runner_task, "runner", kStackWords * 2U, nullptr, kTaskPriority + 1U,
                    &g_runner) != pdPASS) {
        rtos::exit_with(3U);
    }
    vTaskStartScheduler();
    rtos::exit_with(4U);
}

} // extern "C"
