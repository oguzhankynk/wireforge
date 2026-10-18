/*
 * Tests of the byte ring, the frame decoder and the link of the C runtime
 * (runtime/include/wireforge/c). A program without a test framework: every CHECK that fails is
 * reported with its line and the exit code is 1.
 */
#include "wireforge/c/wireforge.h"

#include <stdio.h>
#include <string.h>

static int failures = 0;

#define CHECK(condition)                                                                           \
    do {                                                                                           \
        if (!(condition)) {                                                                        \
            printf("%s:%d: check failed: %s\n", __FILE__, __LINE__, #condition);                   \
            ++failures;                                                                            \
        }                                                                                          \
    } while (0)

/* --- ring ---------------------------------------------------------------------------------- */

static void test_ring(void) {
    uint8_t storage[4];
    uint8_t out[8];
    wf_ring ring;
    wf_ring_init(&ring, storage, sizeof(storage));
    CHECK(ring.size == 0U && wf_ring_free_space(&ring) == 4U);
    const uint8_t first[3] = {1U, 2U, 3U};
    CHECK(wf_ring_write(&ring, first, 3U) == 3U);
    CHECK(wf_ring_write(&ring, first, 3U) == 1U);
    CHECK(ring.size == 4U && wf_ring_free_space(&ring) == 0U);
    CHECK(wf_ring_peek(&ring, out, 8U, 0U) == 4U && out[0] == 1U && out[3] == 1U);
    CHECK(wf_ring_peek(&ring, out, 2U, 1U) == 2U && out[0] == 2U && out[1] == 3U);
    CHECK(wf_ring_peek(&ring, out, 8U, 3U) == 1U);
    CHECK(wf_ring_peek(&ring, out, 8U, 4U) == 0U);
    CHECK(wf_ring_peek(&ring, out, 8U, 9U) == 0U);
    CHECK(wf_ring_consume(&ring, 3U) == 3U && ring.size == 1U);
    const uint8_t second[2] = {7U, 8U};
    CHECK(wf_ring_write(&ring, second, 2U) == 2U); /* wraps around the end of the storage */
    CHECK(wf_ring_read(&ring, out, 8U) == 3U && out[0] == 1U && out[1] == 7U && out[2] == 8U);
    CHECK(ring.size == 0U);
    CHECK(wf_ring_consume(&ring, 5U) == 0U);
    CHECK(wf_ring_write(&ring, first, 0U) == 0U);
    (void)wf_ring_write(&ring, first, 3U);
    wf_ring_reset(&ring);
    CHECK(ring.size == 0U && ring.head == 0U);

    wf_ring empty;
    wf_ring_init(&empty, storage, 0U);
    CHECK(wf_ring_write(&empty, first, 3U) == 0U);
    CHECK(wf_ring_peek(&empty, out, 8U, 0U) == 0U);
    CHECK(wf_ring_consume(&empty, 2U) == 0U);
}

/* --- frame decoder ------------------------------------------------------------------------- */

static uint64_t sum_checksum(const uint8_t* data, size_t length) {
    uint64_t sum = 0U;
    for (size_t index = 0U; index < length; ++index) {
        sum += data[index];
    }
    return sum & 0xFFU;
}

/* Frame: sync 0xA5, length (payload), payload, checksum of header and payload. */
static wf_frame_format sync_format(void) {
    wf_frame_format format = {WF_FRAMING_SYNC_LENGTH,
                              WF_BIG_ENDIAN,
                              2U,
                              1U,
                              16U,
                              {0U, 1U},
                              0xA5U,
                              {1U, 1U},
                              WF_LENGTH_PAYLOAD,
                              0U,
                              WF_COVERS_HEADER_AND_PAYLOAD,
                              true};
    return format;
}

static size_t make_frame(uint8_t* out, uint8_t length, uint8_t fill) {
    out[0] = 0xA5U;
    out[1] = length;
    for (uint8_t index = 0U; index < length; ++index) {
        out[2U + index] = (uint8_t)(fill + index);
    }
    out[2U + length] = (uint8_t)sum_checksum(out, 2U + (size_t)length);
    return 3U + (size_t)length;
}

static void test_frame_decoder(void) {
    uint8_t storage[40];
    uint8_t frame[16];
    uint8_t out[16];
    size_t size = 99U;
    wf_ring ring;
    wf_frame_decoder decoder;
    const wf_frame_format format = sync_format();
    wf_frame_decoder_init(&decoder, &format, sum_checksum, 1U);
    CHECK(wf_frame_decoder_valid(&decoder));
    wf_ring_init(&ring, storage, sizeof(storage));

    CHECK(wf_frame_decoder_next(&decoder, &ring, out, sizeof(out), &size) == WF_TRUNCATED);
    CHECK(size == 0U);
    const size_t length = make_frame(frame, 4U, 10U);
    (void)wf_ring_write(&ring, frame, length - 2U);
    CHECK(wf_frame_decoder_next(&decoder, &ring, out, sizeof(out), &size) == WF_TRUNCATED);
    (void)wf_ring_write(&ring, &frame[length - 2U], 2U);
    CHECK(wf_frame_decoder_next(&decoder, &ring, out, sizeof(out), &size) == WF_OK);
    CHECK(size == length && memcmp(out, frame, length) == 0 && ring.size == 0U);
    CHECK(decoder.counters.frames == 1U);

    /* garbage before the sync byte is skipped */
    const uint8_t junk[3] = {0x00U, 0x11U, 0x22U};
    (void)wf_ring_write(&ring, junk, 3U);
    (void)wf_ring_write(&ring, frame, length);
    CHECK(wf_frame_decoder_next(&decoder, &ring, out, sizeof(out), &size) == WF_OK);
    CHECK(decoder.counters.discarded_bytes == 3U);

    /* a bad checksum: resync drops one byte and the next call carries on */
    (void)wf_ring_write(&ring, frame, length);
    uint8_t broken[16] = {0};
    (void)memcpy(broken, frame, length);
    broken[length - 1U] ^= 0xFFU;
    wf_ring_reset(&ring);
    (void)wf_ring_write(&ring, broken, length);
    CHECK(wf_frame_decoder_next(&decoder, &ring, out, sizeof(out), &size) == WF_CHECKSUM_MISMATCH);
    CHECK(decoder.counters.checksum_errors == 1U && ring.size == length - 1U);
    CHECK(wf_frame_decoder_next(&decoder, &ring, out, sizeof(out), &size) == WF_TRUNCATED);
    wf_ring_reset(&ring);

    /* a length above the maximum */
    const uint8_t long_frame[4] = {0xA5U, 200U, 0U, 0U};
    (void)wf_ring_write(&ring, long_frame, 4U);
    CHECK(wf_frame_decoder_next(&decoder, &ring, out, sizeof(out), &size) == WF_INVALID_LENGTH);
    CHECK(decoder.counters.length_errors == 1U);
    wf_ring_reset(&ring);

    /* a frame that does not fit the output buffer */
    (void)wf_ring_write(&ring, frame, length);
    CHECK(wf_frame_decoder_next(&decoder, &ring, out, 3U, &size) == WF_BUFFER_TOO_SMALL);
    CHECK(decoder.counters.overflow_errors == 1U);
    wf_ring_reset(&ring);

    /* a frame larger than the ring can ever hold */
    uint8_t tiny[6];
    wf_ring small;
    wf_ring_init(&small, tiny, sizeof(tiny));
    const size_t bigger = make_frame(frame, 8U, 1U);
    (void)wf_ring_write(&small, frame, bigger);
    CHECK(wf_frame_decoder_next(&decoder, &small, out, sizeof(out), &size) == WF_INVALID_LENGTH);

    /* without resync a rejected frame flushes the ring */
    wf_frame_format flush = format;
    flush.resync = false;
    wf_frame_decoder flusher;
    wf_frame_decoder_init(&flusher, &flush, sum_checksum, 1U);
    wf_ring_reset(&ring);
    (void)wf_ring_write(&ring, broken, length);
    (void)wf_ring_write(&ring, frame, 0U);
    CHECK(wf_frame_decoder_next(&flusher, &ring, out, sizeof(out), &size) == WF_CHECKSUM_MISMATCH);
    CHECK(ring.size == 0U && flusher.counters.discarded_bytes == length);
}

static void test_frame_formats(void) {
    uint8_t storage[40];
    uint8_t out[40];
    size_t size = 0U;
    wf_ring ring;
    wf_frame_decoder decoder;
    wf_ring_init(&ring, storage, sizeof(storage));

    /* length-prefixed, the length counts payload and trailer, no checksum */
    wf_frame_format prefixed = sync_format();
    prefixed.kind = WF_FRAMING_LENGTH_PREFIXED;
    prefixed.sync.width = 0U;
    prefixed.length_counts = WF_LENGTH_PAYLOAD_AND_TRAILER;
    wf_frame_decoder_init(&decoder, &prefixed, NULL, 4U);
    CHECK(decoder.checksum_size == 0U);
    CHECK(wf_frame_decoder_valid(&decoder));
    const uint8_t first[6] = {0x00U, 4U, 1U, 2U, 3U, 9U};
    (void)wf_ring_write(&ring, first, 6U);
    CHECK(wf_frame_decoder_next(&decoder, &ring, out, sizeof(out), &size) == WF_OK && size == 6U);

    /* the length counts the whole frame */
    wf_frame_format whole = prefixed;
    whole.length_counts = WF_LENGTH_FRAME;
    wf_frame_decoder_init(&decoder, &whole, NULL, 0U);
    const uint8_t second[6] = {0x00U, 6U, 1U, 2U, 3U, 9U};
    (void)wf_ring_write(&ring, second, 6U);
    CHECK(wf_frame_decoder_next(&decoder, &ring, out, sizeof(out), &size) == WF_OK && size == 6U);
    const uint8_t too_short[6] = {0x00U, 2U, 1U, 2U, 3U, 9U};
    (void)wf_ring_write(&ring, too_short, 6U);
    CHECK(wf_frame_decoder_next(&decoder, &ring, out, sizeof(out), &size) == WF_INVALID_LENGTH);
    wf_ring_reset(&ring);

    /* fixed size frames with a checksum that covers the payload only */
    wf_frame_format fixed = {WF_FRAMING_FIXED,
                             WF_LITTLE_ENDIAN,
                             1U,
                             2U,
                             5U,
                             {0U, 1U},
                             0x7EU,
                             {0U, 0U},
                             WF_LENGTH_PAYLOAD,
                             0U,
                             WF_COVERS_PAYLOAD,
                             true};
    wf_frame_decoder_init(&decoder, &fixed, sum_checksum, 2U);
    CHECK(wf_frame_decoder_valid(&decoder));
    const uint8_t good[5] = {0x7EU, 4U, 5U, 9U, 0U};
    (void)wf_ring_write(&ring, good, 5U);
    CHECK(wf_frame_decoder_next(&decoder, &ring, out, sizeof(out), &size) == WF_OK && size == 5U);
}

static void test_invalid_formats(void) {
    uint8_t storage[8];
    uint8_t out[8];
    size_t size = 1U;
    wf_ring ring;
    wf_frame_decoder decoder;
    wf_ring_init(&ring, storage, sizeof(storage));
    wf_frame_format base = sync_format();

    wf_frame_format format = base;
    format.max_frame_size = 2U;
    wf_frame_decoder_init(&decoder, &format, sum_checksum, 1U);
    CHECK(!wf_frame_decoder_valid(&decoder));
    CHECK(wf_frame_decoder_next(&decoder, &ring, out, sizeof(out), &size) == WF_INVALID_STATE);
    CHECK(size == 0U);

    format = base;
    format.max_frame_size = 0U;
    format.header_size = 0U;
    format.trailer_size = 0U;
    format.sync.width = 0U;
    wf_frame_decoder_init(&decoder, &format, NULL, 0U);
    CHECK(!wf_frame_decoder_valid(&decoder));

    format = base;
    format.sync.width = 0U;
    wf_frame_decoder_init(&decoder, &format, sum_checksum, 1U);
    CHECK(!wf_frame_decoder_valid(&decoder)); /* sync-and-length needs a sync field */

    format = base;
    format.length.width = 0U;
    wf_frame_decoder_init(&decoder, &format, sum_checksum, 1U);
    CHECK(!wf_frame_decoder_valid(&decoder));

    format = base;
    format.sync.width = 9U;
    wf_frame_decoder_init(&decoder, &format, sum_checksum, 1U);
    CHECK(!wf_frame_decoder_valid(&decoder));

    format = base;
    format.length.offset = 2U;
    wf_frame_decoder_init(&decoder, &format, sum_checksum, 1U);
    CHECK(!wf_frame_decoder_valid(&decoder));

    format = base;
    format.checksum_offset = 1U;
    wf_frame_decoder_init(&decoder, &format, sum_checksum, 1U);
    CHECK(!wf_frame_decoder_valid(&decoder));

    format = base;
    format.kind = WF_FRAMING_FIXED;
    format.length.width = 0U;
    wf_frame_decoder_init(&decoder, &format, sum_checksum, 1U);
    CHECK(wf_frame_decoder_valid(&decoder));
}

/* --- link ---------------------------------------------------------------------------------- */

typedef struct {
    uint8_t incoming[256];
    size_t incoming_size;
    size_t incoming_at;
    uint8_t outgoing[256];
    size_t outgoing_size;
    size_t send_limit;
    size_t chunk;
} fake_driver;

static size_t fake_send(void* context, const uint8_t* bytes, size_t length) {
    fake_driver* driver = (fake_driver*)context;
    size_t room = driver->send_limit - driver->outgoing_size;
    room = room < length ? room : length;
    for (size_t index = 0U; index < room; ++index) {
        driver->outgoing[driver->outgoing_size + index] = bytes[index];
    }
    driver->outgoing_size += room;
    return room;
}

static size_t fake_receive(void* context, uint8_t* buffer, size_t capacity) {
    fake_driver* driver = (fake_driver*)context;
    size_t count = driver->incoming_size - driver->incoming_at;
    count = count < capacity ? count : capacity;
    count = (driver->chunk != 0U && count > driver->chunk) ? driver->chunk : count;
    for (size_t index = 0U; index < count; ++index) {
        buffer[index] = driver->incoming[driver->incoming_at + index];
    }
    driver->incoming_at += count;
    return count;
}

typedef struct {
    size_t calls;
    size_t last_size;
    wf_status result;
} sink;

/* The dispatch context is const; the sink it points to is not. */
typedef struct {
    sink* target;
} sink_ref;

static wf_status collect(const void* context, const uint8_t* frame, size_t size) {
    sink* target = ((const sink_ref*)context)->target;
    (void)frame;
    ++target->calls;
    target->last_size = size;
    return target->result;
}

static void load(fake_driver* driver, const uint8_t* bytes, size_t length) {
    (void)memset(driver, 0, sizeof(*driver));
    (void)memcpy(driver->incoming, bytes, length);
    driver->incoming_size = length;
    driver->send_limit = sizeof(driver->outgoing);
}

static void answer_with(sink* target, wf_status result) {
    target->result = result;
}

static void limit_sends(fake_driver* driver, size_t limit) {
    driver->send_limit = limit;
}

static void test_framed_link(void) {
    fake_driver driver;
    uint8_t stream[80];
    size_t used = 0U;
    for (uint8_t index = 0U; index < 3U; ++index) {
        used += make_frame(&stream[used], (uint8_t)(index + 1U), index);
    }
    stream[used] = 0x00U; /* junk after the frames */
    load(&driver, stream, used + 1U);
    driver.chunk = 5U;
    const wf_transport transport = {&driver, fake_send, fake_receive};
    uint8_t rx_storage[32];
    uint8_t rx_frame[16];
    uint8_t tx_frame[16];
    sink target = {0U, 0U, WF_OK};
    const sink_ref reference = {&target};
    wf_link link;
    wf_frame_decoder decoder;
    const wf_frame_format format = sync_format();
    wf_frame_decoder_init(&decoder, &format, sum_checksum, 1U);
    wf_link_init(&link, &transport, true, &decoder, rx_storage, sizeof(rx_storage), rx_frame,
                 tx_frame, sizeof(rx_frame), collect, &reference);
    wf_poll_result result = wf_link_poll(&link);
    CHECK(result.frames == 3U && result.errors == 0U && result.last_error == WF_OK);
    CHECK(target.calls == 3U && target.last_size == 6U);
    CHECK(link.counters.rx_bytes == used + 1U);

    /* errors of the dispatcher are counted and remembered */
    load(&driver, stream, used);
    answer_with(&target, WF_UNKNOWN_MESSAGE_ID);
    result = wf_link_poll(&link);
    CHECK(result.frames == 0U && result.errors == 3U && result.last_error == WF_UNKNOWN_MESSAGE_ID);

    /* a rejected frame is an error of the poll too */
    stream[used - 1U] ^= 0xFFU;
    load(&driver, stream, used);
    answer_with(&target, WF_OK);
    result = wf_link_poll(&link);
    CHECK(result.errors >= 1U && result.last_error == WF_CHECKSUM_MISMATCH);

    /* send an encoded frame */
    load(&driver, stream, 0U);
    const uint8_t outgoing[4] = {1U, 2U, 3U, 4U};
    CHECK(wf_link_send_frame(&link, outgoing, 4U) == WF_OK);
    CHECK(driver.outgoing_size == 4U && link.counters.tx_bytes == 4U &&
          link.counters.tx_frames == 1U);
    limit_sends(&driver, 6U);
    CHECK(wf_link_send_frame(&link, outgoing, 4U) == WF_BUFFER_TOO_SMALL);
    CHECK(link.counters.tx_errors == 1U);
    CHECK(wf_link_send_encoded(&link, WF_OUT_OF_RANGE, 0U) == WF_OUT_OF_RANGE);
    CHECK(wf_link_send_encoded(&link, WF_OK, 99U) == WF_BUFFER_TOO_SMALL);
    CHECK(link.counters.tx_errors == 3U);
    limit_sends(&driver, sizeof(driver.outgoing));
    link.tx_frame[0] = 9U;
    link.tx_frame[1] = 8U;
    CHECK(wf_link_send_encoded(&link, WF_OK, 2U) == WF_OK);
    CHECK(driver.outgoing[driver.outgoing_size - 2U] == 9U);
}

static void test_unframed_link(void) {
    fake_driver driver;
    uint8_t bytes[40];
    for (size_t index = 0U; index < sizeof(bytes); ++index) {
        bytes[index] = (uint8_t)index;
    }
    const wf_transport transport = {&driver, fake_send, fake_receive};
    uint8_t rx_storage[16];
    uint8_t rx_frame[16];
    uint8_t tx_frame[16];
    sink target = {0U, 0U, WF_OK};
    const sink_ref reference = {&target};
    wf_link link;
    const wf_frame_format format = sync_format();
    wf_frame_decoder decoder;
    wf_frame_decoder_init(&decoder, &format, NULL, 0U);
    wf_link_init(&link, &transport, false, &decoder, rx_storage, sizeof(rx_storage), rx_frame,
                 tx_frame, sizeof(rx_frame), collect, &reference);

    load(&driver, bytes, 5U);
    wf_poll_result result = wf_link_poll(&link);
    CHECK(result.frames == 1U && target.last_size == 5U);
    result = wf_link_poll(&link);
    CHECK(result.frames == 0U && result.errors == 0U); /* nothing arrived */

    load(&driver, bytes, 16U);
    result = wf_link_poll(&link);
    CHECK(result.frames == 1U && target.last_size == 16U); /* exactly one frame buffer */

    load(&driver, bytes, 40U);
    result = wf_link_poll(&link);
    CHECK(result.frames == 0U && result.errors == 1U && result.last_error == WF_INVALID_LENGTH);
    CHECK(link.counters.rx_bytes == 5U + 16U + 40U);
    CHECK(driver.incoming_at == 40U);
}

int main(void) {
    test_ring();
    test_frame_decoder();
    test_frame_formats();
    test_invalid_formats();
    test_framed_link();
    test_unframed_link();
    if (failures == 0) {
        printf("c link: all checks passed\n");
    }
    return failures == 0 ? 0 : 1;
}
