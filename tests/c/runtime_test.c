/*
 * Tests of the C runtime (runtime/include/wireforge/c). A small program without a test framework:
 * every CHECK that fails is reported with its line and the exit code is the number of failures.
 */
#include "wireforge/c/wireforge.h"

#include <math.h>
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

static const wf_encoding BIG = {WF_BIG_ENDIAN, WF_MSB_FIRST, true, true, false};
static const wf_encoding LITTLE = {WF_LITTLE_ENDIAN, WF_LSB_FIRST, true, true, false};

/* --- status ------------------------------------------------------------------------------- */

static void test_status(void) {
    CHECK(strcmp(wf_status_name(WF_OK), "Ok") == 0);
    CHECK(strcmp(wf_status_name(WF_BUFFER_TOO_SMALL), "BufferTooSmall") == 0);
    CHECK(strcmp(wf_status_name(WF_TRUNCATED), "Truncated") == 0);
    CHECK(strcmp(wf_status_name(WF_OUT_OF_RANGE), "OutOfRange") == 0);
    CHECK(strcmp(wf_status_name(WF_COUNT_EXCEEDS_MAX), "CountExceedsMax") == 0);
    CHECK(strcmp(wf_status_name(WF_CONST_MISMATCH), "ConstMismatch") == 0);
    CHECK(strcmp(wf_status_name(WF_CHECKSUM_MISMATCH), "ChecksumMismatch") == 0);
    CHECK(strcmp(wf_status_name(WF_UNKNOWN_MESSAGE_ID), "UnknownMessageId") == 0);
    CHECK(strcmp(wf_status_name(WF_INVALID_LENGTH), "InvalidLength") == 0);
    CHECK(strcmp(wf_status_name(WF_TRAILING_BYTES), "TrailingBytes") == 0);
    CHECK(strcmp(wf_status_name(WF_INVALID_STATE), "InvalidState") == 0);
    CHECK(strcmp(wf_status_name(11U), "Unknown") == 0);
    CHECK(strcmp(wf_status_name(255U), "Unknown") == 0);
}

/* --- bits --------------------------------------------------------------------------------- */

static void test_masks_and_fits(void) {
    CHECK(wf_low_mask(0U) == 0U);
    CHECK(wf_low_mask(1U) == 1U);
    CHECK(wf_low_mask(12U) == 0xFFFU);
    CHECK(wf_low_mask(63U) == 0x7FFFFFFFFFFFFFFFULL);
    CHECK(wf_low_mask(64U) == 0xFFFFFFFFFFFFFFFFULL);
    CHECK(wf_low_mask(70U) == 0xFFFFFFFFFFFFFFFFULL);
    CHECK(wf_fits_unsigned(15U, 4U));
    CHECK(!wf_fits_unsigned(16U, 4U));
    CHECK(wf_fits_unsigned(0xFFFFFFFFFFFFFFFFULL, 64U));
    CHECK(wf_fits_signed(-8, 4U));
    CHECK(wf_fits_signed(7, 4U));
    CHECK(!wf_fits_signed(8, 4U));
    CHECK(!wf_fits_signed(-9, 4U));
    CHECK(!wf_fits_signed(0, 0U));
    CHECK(wf_fits_signed(INT64_MIN, 64U));
    CHECK(wf_to_twos_complement(-1, 4U) == 0xFU);
    CHECK(wf_to_twos_complement(-2, 12U) == 0xFFEU);
    CHECK(wf_sign_extend(0xFU, 4U) == -1);
    CHECK(wf_sign_extend(0x7U, 4U) == 7);
    CHECK(wf_sign_extend(0x8U, 4U) == -8);
    CHECK(wf_sign_extend(0xFFFFFFFFFFFFFFFFULL, 64U) == -1);
    CHECK(wf_sign_extend(0x8000000000000000ULL, 64U) == INT64_MIN);
    CHECK(wf_sign_extend(0x1F5U, 4U) == 5);
    CHECK(wf_reflect(0x01U, 8U) == 0x80U);
    CHECK(wf_reflect(0x0BU, 4U) == 0x0DU);
    CHECK(wf_reflect(0U, 16U) == 0U);
}

static void test_packer(void) {
    wf_bit_packer packer;
    wf_packer_init(&packer, 16U, WF_MSB_FIRST);
    CHECK(!wf_packer_complete(&packer));
    CHECK(wf_packer_put(&packer, 0x5U, 4U) == WF_OK);
    CHECK(wf_packer_put_signed(&packer, -2, 4U) == WF_OK);
    CHECK(wf_packer_put(&packer, 0xABU, 8U) == WF_OK);
    CHECK(wf_packer_complete(&packer));
    CHECK(packer.container == 0x5EABU);
    CHECK(wf_packer_put(&packer, 1U, 1U) == WF_INVALID_STATE);

    wf_packer_init(&packer, 16U, WF_LSB_FIRST);
    CHECK(wf_packer_put(&packer, 0x5U, 4U) == WF_OK);
    CHECK(wf_packer_put_signed(&packer, -2, 4U) == WF_OK);
    CHECK(wf_packer_put(&packer, 0xABU, 8U) == WF_OK);
    CHECK(packer.container == 0xABE5U);

    wf_packer_init(&packer, 8U, WF_MSB_FIRST);
    CHECK(wf_packer_put(&packer, 16U, 4U) == WF_OUT_OF_RANGE);
    CHECK(wf_packer_put(&packer, 1U, 0U) == WF_INVALID_STATE);
    CHECK(wf_packer_put_signed(&packer, 8, 4U) == WF_OUT_OF_RANGE);
    CHECK(wf_packer_put_signed(&packer, 0, 0U) == WF_INVALID_STATE);
    CHECK(wf_packer_put(&packer, 1U, 9U) == WF_INVALID_STATE);
    wf_packer_init(&packer, 12U, WF_MSB_FIRST);
    CHECK(!wf_group_width_valid(12U));
    CHECK(wf_packer_put(&packer, 1U, 4U) == WF_INVALID_STATE);
    CHECK(!wf_packer_complete(&packer));
    CHECK(!wf_group_width_valid(0U));
    CHECK(!wf_group_width_valid(72U));
    CHECK(wf_group_width_valid(64U));
}

static void test_unpacker(void) {
    wf_bit_unpacker unpacker;
    uint64_t value = 99U;
    int64_t signed_value = 99;
    wf_unpacker_init(&unpacker, 0x5EABU, 16U, WF_MSB_FIRST);
    CHECK(wf_unpacker_get(&unpacker, &value, 4U) == WF_OK && value == 5U);
    CHECK(wf_unpacker_get_signed(&unpacker, &signed_value, 4U) == WF_OK && signed_value == -2);
    CHECK(wf_unpacker_get(&unpacker, &value, 8U) == WF_OK && value == 0xABU);
    value = 7U;
    CHECK(wf_unpacker_get(&unpacker, &value, 1U) == WF_INVALID_STATE && value == 7U);
    signed_value = 7;
    CHECK(wf_unpacker_get_signed(&unpacker, &signed_value, 1U) == WF_INVALID_STATE);
    CHECK(signed_value == 7);

    wf_unpacker_init(&unpacker, 0xABE5U, 16U, WF_LSB_FIRST);
    CHECK(wf_unpacker_get(&unpacker, &value, 4U) == WF_OK && value == 5U);
    CHECK(wf_unpacker_get_signed(&unpacker, &signed_value, 4U) == WF_OK && signed_value == -2);
    CHECK(wf_unpacker_get(&unpacker, &value, 0U) == WF_INVALID_STATE);
    wf_unpacker_init(&unpacker, 0U, 10U, WF_LSB_FIRST);
    CHECK(wf_unpacker_get(&unpacker, &value, 4U) == WF_INVALID_STATE);
}

/* --- bytes -------------------------------------------------------------------------------- */

static void test_store_and_load(void) {
    uint8_t buffer[8] = {0};
    wf_store_unsigned(buffer, 0x0102030405060708ULL, 8U, WF_BIG_ENDIAN);
    CHECK(buffer[0] == 1U && buffer[7] == 8U);
    CHECK(wf_load_unsigned(buffer, 8U, WF_BIG_ENDIAN) == 0x0102030405060708ULL);
    wf_store_unsigned(buffer, 0x0102030405060708ULL, 8U, WF_LITTLE_ENDIAN);
    CHECK(buffer[0] == 8U && buffer[7] == 1U);
    CHECK(wf_load_unsigned(buffer, 8U, WF_LITTLE_ENDIAN) == 0x0102030405060708ULL);
    wf_store_unsigned(buffer, 0xABCDEFU, 3U, WF_BIG_ENDIAN);
    CHECK(buffer[0] == 0xABU && buffer[1] == 0xCDU && buffer[2] == 0xEFU);
    CHECK(wf_load_unsigned(buffer, 3U, WF_LITTLE_ENDIAN) == 0xEFCDABU);
    CHECK(wf_valid_width(1U) && wf_valid_width(8U));
    CHECK(!wf_valid_width(0U) && !wf_valid_width(9U));
    CHECK(wf_width_bits(3U) == 24U);
}

static void test_writer(void) {
    uint8_t buffer[8];
    wf_writer writer;
    memset(buffer, 0xEE, sizeof(buffer));
    wf_writer_init(&writer, buffer, sizeof(buffer));
    CHECK(wf_writer_remaining(&writer) == 8U);
    CHECK(wf_write_unsigned(&writer, 0x1234U, 2U, WF_BIG_ENDIAN) == WF_OK);
    CHECK(buffer[0] == 0x12U && buffer[1] == 0x34U && writer.position == 2U);
    CHECK(wf_write_signed(&writer, -2, 2U, WF_LITTLE_ENDIAN) == WF_OK);
    CHECK(buffer[2] == 0xFEU && buffer[3] == 0xFFU);
    CHECK(wf_write_unsigned(&writer, 0x100U, 1U, WF_BIG_ENDIAN) == WF_OUT_OF_RANGE);
    CHECK(wf_write_signed(&writer, 128, 1U, WF_BIG_ENDIAN) == WF_OUT_OF_RANGE);
    CHECK(wf_write_unsigned(&writer, 1U, 0U, WF_BIG_ENDIAN) == WF_INVALID_STATE);
    CHECK(wf_write_signed(&writer, 1, 9U, WF_BIG_ENDIAN) == WF_INVALID_STATE);
    CHECK(wf_write_unsigned(&writer, 1U, 8U, WF_BIG_ENDIAN) == WF_BUFFER_TOO_SMALL);
    CHECK(writer.position == 4U);
    CHECK(wf_write_f32(&writer, 1.5F, WF_BIG_ENDIAN) == WF_OK);
    CHECK(buffer[4] == 0x3FU && buffer[5] == 0xC0U && buffer[6] == 0U && buffer[7] == 0U);
    CHECK(wf_write_f32(&writer, 1.0F, WF_BIG_ENDIAN) == WF_BUFFER_TOO_SMALL);
    CHECK(wf_write_unsigned_at(&writer, 0U, 0xABCDU, 2U, WF_BIG_ENDIAN) == WF_OK);
    CHECK(buffer[0] == 0xABU && buffer[1] == 0xCDU);
    CHECK(wf_write_unsigned_at(&writer, 7U, 1U, 2U, WF_BIG_ENDIAN) == WF_INVALID_LENGTH);
    CHECK(wf_write_unsigned_at(&writer, 9U, 1U, 1U, WF_BIG_ENDIAN) == WF_INVALID_LENGTH);
    CHECK(wf_write_unsigned_at(&writer, 0U, 0x10000U, 2U, WF_BIG_ENDIAN) == WF_OUT_OF_RANGE);
    CHECK(wf_write_unsigned_at(&writer, 0U, 1U, 0U, WF_BIG_ENDIAN) == WF_INVALID_STATE);

    wf_writer_init(&writer, buffer, sizeof(buffer));
    CHECK(wf_write_f64(&writer, -2.0, WF_LITTLE_ENDIAN) == WF_OK);
    CHECK(buffer[7] == 0xC0U && buffer[0] == 0U);
    CHECK(wf_write_f64(&writer, 1.0, WF_LITTLE_ENDIAN) == WF_BUFFER_TOO_SMALL);

    wf_writer_init(&writer, buffer, sizeof(buffer));
    const uint8_t bytes[3] = {1U, 2U, 3U};
    CHECK(wf_write_bytes(&writer, bytes, 3U) == WF_OK && writer.position == 3U);
    CHECK(wf_write_bytes(&writer, bytes, 6U) == WF_BUFFER_TOO_SMALL && writer.position == 3U);
    CHECK(wf_write_bytes(&writer, bytes, 0U) == WF_OK);

    wf_bit_packer packer;
    wf_writer_init(&writer, buffer, sizeof(buffer));
    wf_packer_init(&packer, 16U, WF_MSB_FIRST);
    CHECK(wf_write_bits(&writer, &packer, WF_BIG_ENDIAN) == WF_INVALID_STATE);
    CHECK(wf_packer_put(&packer, 0xBEEFU, 16U) == WF_OK);
    CHECK(wf_write_bits(&writer, &packer, WF_LITTLE_ENDIAN) == WF_OK);
    CHECK(buffer[0] == 0xEFU && buffer[1] == 0xBEU);
}

static void test_reader(void) {
    const uint8_t input[12] = {0x12U, 0x34U, 0xFEU, 0xFFU, 0x3FU, 0xC0U,
                               0x00U, 0x00U, 0x01U, 0x02U, 0x03U, 0x04U};
    wf_reader reader;
    uint64_t value = 0U;
    int64_t signed_value = 0;
    float single = 0.0F;
    double wide = 0.0;
    wf_reader_init(&reader, input, sizeof(input));
    CHECK(wf_reader_remaining(&reader) == 12U);
    CHECK(wf_read_unsigned(&reader, &value, 2U, WF_BIG_ENDIAN) == WF_OK && value == 0x1234U);
    CHECK(wf_read_signed(&reader, &signed_value, 2U, WF_LITTLE_ENDIAN) == WF_OK);
    CHECK(signed_value == -2);
    CHECK(wf_read_f32(&reader, &single, WF_BIG_ENDIAN) == WF_OK && single == 1.5F);
    uint8_t out[2] = {0U, 0U};
    CHECK(wf_read_bytes(&reader, out, 2U) == WF_OK && out[0] == 1U && out[1] == 2U);
    CHECK(wf_read_skip(&reader, 1U) == WF_OK);
    CHECK(wf_read_bytes(&reader, out, 2U) == WF_TRUNCATED);
    CHECK(wf_read_skip(&reader, 2U) == WF_TRUNCATED);
    CHECK(wf_reader_remaining(&reader) == 1U);
    value = 5U;
    CHECK(wf_read_unsigned(&reader, &value, 2U, WF_BIG_ENDIAN) == WF_TRUNCATED && value == 5U);
    signed_value = 5;
    CHECK(wf_read_signed(&reader, &signed_value, 2U, WF_BIG_ENDIAN) == WF_TRUNCATED);
    CHECK(signed_value == 5);
    CHECK(wf_read_unsigned(&reader, &value, 0U, WF_BIG_ENDIAN) == WF_INVALID_STATE);
    CHECK(wf_read_f32(&reader, &single, WF_BIG_ENDIAN) == WF_TRUNCATED);
    CHECK(wf_read_f64(&reader, &wide, WF_BIG_ENDIAN) == WF_TRUNCATED);

    const uint8_t eight[8] = {0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0xF0U, 0x3FU};
    wf_reader_init(&reader, eight, sizeof(eight));
    CHECK(wf_read_f64(&reader, &wide, WF_LITTLE_ENDIAN) == WF_OK && wide == 1.0);

    const uint8_t group[3] = {0xABU, 0xCDU, 0xEFU};
    wf_bit_unpacker unpacker;
    wf_reader_init(&reader, group, sizeof(group));
    CHECK(wf_read_bits(&reader, &unpacker, 12U, WF_MSB_FIRST, WF_BIG_ENDIAN) == WF_INVALID_STATE);
    CHECK(wf_read_bits(&reader, &unpacker, 24U, WF_MSB_FIRST, WF_BIG_ENDIAN) == WF_OK);
    CHECK(unpacker.container == 0xABCDEFU && unpacker.total_bits == 24U);
    CHECK(wf_read_bits(&reader, &unpacker, 8U, WF_MSB_FIRST, WF_BIG_ENDIAN) == WF_TRUNCATED);
}

/* --- encoder ------------------------------------------------------------------------------ */

static uint64_t sum_checksum(const uint8_t* data, size_t length) {
    uint64_t sum = 0U;
    for (size_t index = 0U; index < length; ++index) {
        sum += data[index];
    }
    return sum;
}

static void test_encoder(void) {
    uint8_t buffer[32];
    wf_encoder encoder;
    memset(buffer, 0, sizeof(buffer));
    wf_encoder_init(&encoder, buffer, sizeof(buffer), &BIG);
    wf_enc_unsigned(&encoder, 0x0102U, 2U);
    wf_enc_signed(&encoder, -1, 1U);
    wf_enc_bool(&encoder, true);
    wf_enc_bool(&encoder, false);
    wf_enc_f32(&encoder, 1.5F);
    wf_enc_f64(&encoder, 2.0);
    CHECK(encoder.status == WF_OK);
    CHECK(wf_encoder_position(&encoder) == 2U + 1U + 2U + 4U + 8U);
    CHECK(buffer[0] == 1U && buffer[1] == 2U && buffer[2] == 0xFFU && buffer[3] == 1U);
    CHECK(buffer[4] == 0U && buffer[5] == 0x3FU && buffer[6] == 0xC0U);
    CHECK(buffer[9] == 0x40U);

    wf_encoder_init(&encoder, buffer, sizeof(buffer), &BIG);
    wf_enc_count(&encoder, 3U, 255U, 1U);
    wf_enc_count(&encoder, 1U, 1U, 1U);
    CHECK(encoder.status == WF_OK && buffer[0] == 3U && buffer[1] == 1U);
    wf_enc_count(&encoder, 256U, 255U, 1U);
    CHECK(encoder.status == WF_OUT_OF_RANGE);
    wf_enc_unsigned(&encoder, 1U, 1U);
    CHECK(wf_encoder_position(&encoder) == 2U);
    wf_enc_check(&encoder, WF_TRUNCATED);
    CHECK(encoder.status == WF_OUT_OF_RANGE);

    wf_encoder_init(&encoder, buffer, sizeof(buffer), &BIG);
    wf_enc_text(&encoder, "ab", 2U, 4U);
    CHECK(buffer[0] == 'a' && buffer[1] == 'b' && buffer[2] == 0U && buffer[3] == 0U);
    CHECK(wf_encoder_position(&encoder) == 4U);

    wf_encoder_init(&encoder, buffer, sizeof(buffer), &BIG);
    wf_bit_packer group;
    wf_enc_group(&encoder, &group, 16U);
    wf_enc_put(&encoder, &group, 5U, 4U);
    wf_enc_put_signed(&encoder, &group, -2, 4U);
    wf_enc_put(&encoder, &group, 0xABU, 8U);
    wf_enc_write_bits(&encoder, &group);
    CHECK(encoder.status == WF_OK && buffer[0] == 0x5EU && buffer[1] == 0xABU);
    wf_encoder_init(&encoder, buffer, sizeof(buffer), &LITTLE);
    wf_enc_group(&encoder, &group, 16U);
    wf_enc_put(&encoder, &group, 5U, 4U);
    wf_enc_put_signed(&encoder, &group, -2, 4U);
    wf_enc_put(&encoder, &group, 0xABU, 8U);
    wf_enc_write_bits(&encoder, &group);
    CHECK(encoder.status == WF_OK && buffer[0] == 0xE5U && buffer[1] == 0xABU);
    wf_enc_put(&encoder, &group, 1U, 1U);
    CHECK(encoder.status == WF_INVALID_STATE);
    wf_enc_write_bits(&encoder, &group);
    wf_encoder_init(&encoder, buffer, sizeof(buffer), &BIG);
    wf_enc_group(&encoder, &group, 8U);
    wf_enc_put(&encoder, &group, 1U, 4U);
    wf_enc_write_bits(&encoder, &group);
    CHECK(encoder.status == WF_INVALID_STATE);

    wf_encoder_init(&encoder, buffer, sizeof(buffer), &BIG);
    const uint8_t raw[3] = {7U, 8U, 9U};
    wf_enc_bytes(&encoder, raw, 3U);
    wf_enc_checksum(&encoder, 0U, 3U, sum_checksum, 2U);
    CHECK(encoder.status == WF_OK && buffer[3] == 0U && buffer[4] == 24U);
    wf_enc_bytes(&encoder, raw, 3U);
    wf_enc_checksum(&encoder, 0U, 3U, sum_checksum, 2U);
    CHECK(wf_encoder_position(&encoder) == 10U);

    wf_encoder_init(&encoder, buffer, 2U, &BIG);
    wf_enc_unsigned(&encoder, 1U, 4U);
    CHECK(encoder.status == WF_BUFFER_TOO_SMALL);
    wf_enc_f32(&encoder, 1.0F);
    wf_enc_f64(&encoder, 1.0);
    wf_enc_signed(&encoder, 1, 1U);
    wf_enc_bytes(&encoder, raw, 1U);
    wf_enc_checksum(&encoder, 0U, 0U, sum_checksum, 1U);
    CHECK(wf_encoder_position(&encoder) == 0U);

    wf_encoding quiet = BIG;
    quiet.check_encode = false;
    wf_encoder_init(&encoder, buffer, sizeof(buffer), &quiet);
    wf_enc_check_value(&encoder, WF_OUT_OF_RANGE);
    CHECK(encoder.status == WF_OK);
    wf_encoder_init(&encoder, buffer, sizeof(buffer), &BIG);
    wf_enc_check_value(&encoder, WF_OUT_OF_RANGE);
    CHECK(encoder.status == WF_OUT_OF_RANGE);
}

/* --- decoder ------------------------------------------------------------------------------ */

static void test_decoder(void) {
    const uint8_t input[] = {0x01U, 0x02U, 0xFFU, 0x01U, 0x00U, 0x3FU, 0xC0U, 0x00U, 0x00U,
                             0x40U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x05U};
    wf_decoder decoder;
    wf_decoder_init(&decoder, input, sizeof(input), &BIG);
    CHECK(wf_dec_unsigned(&decoder, 2U) == 0x0102U);
    CHECK(wf_dec_signed(&decoder, 1U) == -1);
    CHECK(wf_dec_bool(&decoder));
    CHECK(!wf_dec_bool(&decoder));
    CHECK(wf_dec_f32(&decoder) == 1.5F);
    CHECK(wf_dec_f64(&decoder) == 2.0);
    CHECK(wf_dec_count(&decoder, 1U) == 5U);
    CHECK(decoder.status == WF_OK && wf_decoder_remaining(&decoder) == 0U);
    CHECK(wf_decoder_position(&decoder) == sizeof(input));
    CHECK(wf_dec_unsigned(&decoder, 1U) == 0U && decoder.status == WF_TRUNCATED);
    CHECK(wf_dec_signed(&decoder, 1U) == 0);
    CHECK(wf_dec_f32(&decoder) == 0.0F);
    CHECK(wf_dec_f64(&decoder) == 0.0);
    wf_dec_check(&decoder, WF_OUT_OF_RANGE);
    CHECK(decoder.status == WF_TRUNCATED);

    const uint8_t bad_bool[] = {2U};
    wf_decoder_init(&decoder, bad_bool, 1U, &BIG);
    CHECK(!wf_dec_bool(&decoder));
    CHECK(decoder.status == WF_OUT_OF_RANGE);

    const uint8_t consts[] = {0x12U, 0xFEU, 0x01U, 0x3FU, 0xC0U, 0x00U, 0x00U, 0x40U,
                              0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U};
    wf_decoder_init(&decoder, consts, sizeof(consts), &BIG);
    wf_dec_expect_unsigned(&decoder, 0x12U, 1U);
    wf_dec_expect_signed(&decoder, -2, 1U);
    wf_dec_expect_bool(&decoder, true);
    wf_dec_expect_f32(&decoder, 1.5F);
    wf_dec_expect_f64(&decoder, 2.0);
    CHECK(decoder.status == WF_OK);
    const uint8_t again[] = {0x12U, 0xFEU, 0x01U, 0x3FU, 0xC0U, 0x00U, 0x00U, 0x40U,
                             0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U, 0x00U};
    wf_decoder_init(&decoder, again, sizeof(again), &BIG);
    wf_dec_expect_unsigned(&decoder, 0x13U, 1U);
    CHECK(decoder.status == WF_CONST_MISMATCH);
    wf_decoder_init(&decoder, again, sizeof(again), &BIG);
    wf_dec_skip(&decoder, 1U);
    wf_dec_expect_signed(&decoder, 5, 1U);
    CHECK(decoder.status == WF_CONST_MISMATCH);
    wf_decoder_init(&decoder, again, sizeof(again), &BIG);
    wf_dec_skip(&decoder, 2U);
    wf_dec_expect_bool(&decoder, false);
    CHECK(decoder.status == WF_CONST_MISMATCH);
    wf_decoder_init(&decoder, again, sizeof(again), &BIG);
    wf_dec_skip(&decoder, 3U);
    wf_dec_expect_f32(&decoder, 2.5F);
    CHECK(decoder.status == WF_CONST_MISMATCH);
    wf_decoder_init(&decoder, again, sizeof(again), &BIG);
    wf_dec_skip(&decoder, 7U);
    wf_dec_expect_f64(&decoder, 3.0);
    CHECK(decoder.status == WF_CONST_MISMATCH);
    wf_dec_skip(&decoder, 100U);
    wf_dec_expect_unsigned(&decoder, 1U, 1U);
    CHECK(decoder.status == WF_CONST_MISMATCH);
    wf_decoder_init(&decoder, again, 1U, &BIG);
    wf_dec_skip(&decoder, 2U);
    CHECK(decoder.status == WF_TRUNCATED);
    wf_dec_skip(&decoder, 1U);
    wf_dec_expect_unsigned(&decoder, 0U, 1U);
    wf_dec_expect_signed(&decoder, 0, 1U);
    wf_dec_expect_bool(&decoder, false);
    wf_dec_expect_f32(&decoder, 0.0F);
    wf_dec_expect_f64(&decoder, 0.0);
    CHECK(decoder.status == WF_TRUNCATED);

    wf_encoding quiet = BIG;
    quiet.check_decode = false;
    wf_decoder_init(&decoder, again, 1U, &quiet);
    wf_dec_check_value(&decoder, WF_OUT_OF_RANGE);
    CHECK(decoder.status == WF_OK);
    wf_decoder_init(&decoder, again, 1U, &BIG);
    wf_dec_check_value(&decoder, WF_OUT_OF_RANGE);
    CHECK(decoder.status == WF_OUT_OF_RANGE);
}

static void test_decoder_text_and_counts(void) {
    const uint8_t input[] = {'a', 'b', 0U, 0U, 'c', 'd', 'e', 0U};
    char text[8];
    size_t length = 99U;
    wf_decoder decoder;
    wf_decoder_init(&decoder, input, sizeof(input), &BIG);
    wf_dec_text(&decoder, text, sizeof(text), &length, 4U, true);
    CHECK(decoder.status == WF_OK && length == 2U && text[0] == 'a' && text[1] == 'b');
    wf_dec_text(&decoder, text, sizeof(text), &length, 4U, false);
    CHECK(decoder.status == WF_OK && length == 4U && text[2] == 'e');
    wf_decoder_init(&decoder, input, sizeof(input), &BIG);
    wf_dec_text(&decoder, text, 3U, &length, 4U, true);
    CHECK(decoder.status == WF_COUNT_EXCEEDS_MAX);
    wf_dec_text(&decoder, text, sizeof(text), &length, 4U, true);
    wf_decoder_init(&decoder, input, 2U, &BIG);
    wf_dec_text(&decoder, text, sizeof(text), &length, 4U, true);
    CHECK(decoder.status == WF_TRUNCATED);

    wf_decoder_init(&decoder, input, sizeof(input), &BIG);
    wf_dec_count_in(&decoder, 3U, 1U, 5U);
    CHECK(decoder.status == WF_OK);
    wf_dec_count_in(&decoder, 0U, 1U, 5U);
    CHECK(decoder.status == WF_COUNT_EXCEEDS_MAX);
    wf_decoder_init(&decoder, input, sizeof(input), &BIG);
    wf_dec_count_in(&decoder, 6U, 1U, 5U);
    CHECK(decoder.status == WF_COUNT_EXCEEDS_MAX);

    wf_decoder_init(&decoder, input, sizeof(input), &BIG);
    CHECK(wf_dec_fill_count(&decoder, 2U) == 4U && decoder.status == WF_OK);
    CHECK(wf_dec_fill_count(&decoder, 3U) == 0U && decoder.status == WF_INVALID_LENGTH);
    wf_decoder_init(&decoder, input, sizeof(input), &BIG);
    CHECK(wf_dec_fill_count(&decoder, 0U) == 0U && decoder.status == WF_INVALID_LENGTH);

    wf_decoder_init(&decoder, input, sizeof(input), &BIG);
    uint8_t out[3];
    wf_dec_bytes(&decoder, out, 3U);
    CHECK(decoder.status == WF_OK && out[0] == 'a');
    wf_dec_bytes(&decoder, out, 9U);
    CHECK(decoder.status == WF_TRUNCATED);
    wf_dec_bytes(&decoder, out, 1U);

    wf_decoder_init(&decoder, input, sizeof(input), &BIG);
    wf_dec_skip(&decoder, 7U);
    wf_dec_finish(&decoder);
    CHECK(decoder.status == WF_TRAILING_BYTES);
    wf_encoding lenient = BIG;
    lenient.allow_trailing_bytes = true;
    wf_decoder_init(&decoder, input, sizeof(input), &lenient);
    wf_dec_finish(&decoder);
    CHECK(decoder.status == WF_OK);
    wf_dec_skip(&decoder, 8U);
    wf_dec_finish(&decoder);
    CHECK(decoder.status == WF_OK);
}

static void test_decoder_bit_groups(void) {
    const uint8_t input[] = {0x5EU, 0xABU, 0x5EU, 0xABU, 0x5EU, 0xABU, 0x50U};
    wf_decoder decoder;
    wf_bit_unpacker group;
    wf_decoder_init(&decoder, input, sizeof(input), &BIG);
    wf_dec_group(&decoder, &group, 16U);
    CHECK(wf_dec_get(&decoder, &group, 4U) == 5U);
    CHECK(wf_dec_get_signed(&decoder, &group, 4U) == -2);
    CHECK(wf_dec_get(&decoder, &group, 8U) == 0xABU);
    CHECK(decoder.status == WF_OK);
    wf_dec_group(&decoder, &group, 16U);
    wf_dec_expect_bits(&decoder, &group, 5U, 4U);
    wf_dec_expect_bits_signed(&decoder, &group, -2, 4U);
    wf_dec_skip_bits(&decoder, &group, 8U);
    CHECK(decoder.status == WF_OK);
    wf_dec_group(&decoder, &group, 16U);
    wf_dec_expect_bits(&decoder, &group, 6U, 4U);
    CHECK(decoder.status == WF_CONST_MISMATCH);

    wf_decoder_init(&decoder, input, sizeof(input), &BIG);
    wf_dec_group(&decoder, &group, 16U);
    wf_dec_expect_bits_signed(&decoder, &group, 3, 4U);
    CHECK(decoder.status == WF_CONST_MISMATCH);
    wf_dec_group(&decoder, &group, 16U);
    wf_dec_skip_bits(&decoder, &group, 1U);
    CHECK(decoder.status == WF_CONST_MISMATCH);

    wf_decoder_init(&decoder, input, 1U, &BIG);
    wf_dec_group(&decoder, &group, 16U);
    CHECK(decoder.status == WF_TRUNCATED);
    wf_dec_expect_bits(&decoder, &group, 0U, 4U);
    wf_dec_expect_bits_signed(&decoder, &group, 0, 4U);
    wf_decoder_init(&decoder, input, sizeof(input), &BIG);
    wf_dec_group(&decoder, &group, 12U);
    CHECK(decoder.status == WF_INVALID_STATE);
}

static void test_decoder_checksum(void) {
    const uint8_t good[] = {7U, 8U, 9U, 0U, 24U};
    const uint8_t bad[] = {7U, 8U, 9U, 0U, 25U};
    wf_decoder decoder;
    wf_decoder_init(&decoder, good, sizeof(good), &BIG);
    wf_dec_skip(&decoder, 3U);
    wf_dec_verify_checksum(&decoder, 0U, 3U, sum_checksum, 2U);
    CHECK(decoder.status == WF_OK);
    wf_decoder_init(&decoder, bad, sizeof(bad), &BIG);
    wf_dec_skip(&decoder, 3U);
    wf_dec_verify_checksum(&decoder, 0U, 3U, sum_checksum, 2U);
    CHECK(decoder.status == WF_CHECKSUM_MISMATCH);
    wf_decoder_init(&decoder, good, 4U, &BIG);
    wf_dec_skip(&decoder, 3U);
    wf_dec_verify_checksum(&decoder, 0U, 3U, sum_checksum, 2U);
    CHECK(decoder.status == WF_TRUNCATED);
}

/* --- validation, expressions, CRC --------------------------------------------------------- */

static void test_validation(void) {
    CHECK(wf_check_min_s(5, 5) == WF_OK && wf_check_min_s(4, 5) == WF_OUT_OF_RANGE);
    CHECK(wf_check_max_s(5, 5) == WF_OK && wf_check_max_s(6, 5) == WF_OUT_OF_RANGE);
    CHECK(wf_check_range_s(5, 1, 9) == WF_OK && wf_check_range_s(0, 1, 9) == WF_OUT_OF_RANGE);
    CHECK(wf_check_range_s(10, 1, 9) == WF_OUT_OF_RANGE);
    CHECK(wf_check_min_u(5U, 5) == WF_OK && wf_check_min_u(4U, 5) == WF_OUT_OF_RANGE);
    CHECK(wf_check_min_u(0U, -3) == WF_OK);
    CHECK(wf_check_max_u(5U, 5) == WF_OK && wf_check_max_u(6U, 5) == WF_OUT_OF_RANGE);
    CHECK(wf_check_max_u(0U, -1) == WF_OUT_OF_RANGE);
    CHECK(wf_check_max_u(UINT64_MAX, INT64_MAX) == WF_OUT_OF_RANGE);
    CHECK(wf_check_range_u(5U, 1, 9) == WF_OK && wf_check_range_u(0U, 1, 9) == WF_OUT_OF_RANGE);
    CHECK(wf_check_range_u(10U, 1, 9) == WF_OUT_OF_RANGE);
    CHECK(wf_check_min_f(1.0, 1.0) == WF_OK && wf_check_min_f(0.5, 1.0) == WF_OUT_OF_RANGE);
    CHECK(wf_check_max_f(1.0, 1.0) == WF_OK && wf_check_max_f(1.5, 1.0) == WF_OUT_OF_RANGE);
    CHECK(wf_check_range_f(1.0, 0.0, 2.0) == WF_OK);
    CHECK(wf_check_range_f(-1.0, 0.0, 2.0) == WF_OUT_OF_RANGE);
    CHECK(wf_check_range_f(3.0, 0.0, 2.0) == WF_OUT_OF_RANGE);
    CHECK(wf_check_range_f((double)NAN, 0.0, 2.0) == WF_OUT_OF_RANGE);
    CHECK(wf_check_max_f((double)NAN, 2.0) == WF_OUT_OF_RANGE);
    CHECK(wf_check_bits_s(-8, 4U) == WF_OK && wf_check_bits_s(8, 4U) == WF_OUT_OF_RANGE);
    CHECK(wf_check_bits_u(15U, 4U) == WF_OK && wf_check_bits_u(16U, 4U) == WF_OUT_OF_RANGE);
    CHECK(wf_check_true(true) == WF_OK && wf_check_true(false) == WF_OUT_OF_RANGE);
    CHECK(wf_check_count(3U, 1U, 5U) == WF_OK);
    CHECK(wf_check_count(0U, 1U, 5U) == WF_COUNT_EXCEEDS_MAX);
    CHECK(wf_check_count(6U, 1U, 5U) == WF_COUNT_EXCEEDS_MAX);
    CHECK(wf_check_count_equals(3U, 3) == WF_OK);
    CHECK(wf_check_count_equals(3U, 4) == WF_COUNT_EXCEEDS_MAX);
    CHECK(wf_check_count_equals(0U, -1) == WF_COUNT_EXCEEDS_MAX);
}

static void test_expressions(void) {
    CHECK(wf_expr_min(3, 5) == 3 && wf_expr_min(5, 3) == 3);
    CHECK(wf_expr_max(3, 5) == 5 && wf_expr_max(5, 3) == 5);
    CHECK(wf_expr_ceil_div(6, 3) == 2);
    CHECK(wf_expr_ceil_div(7, 3) == 3);
    CHECK(wf_expr_ceil_div(-7, 3) == -2);
    CHECK(wf_expr_ceil_div(7, -3) == -2);
    CHECK(wf_expr_ceil_div(-7, -3) == 3);
    CHECK(wf_expr_ceil_div(1, 0) == 0);
}

static void test_crc(void) {
    const uint8_t check[] = {'1', '2', '3', '4', '5', '6', '7', '8', '9'};
    static const wf_crc_params crc32 = {32U, 0x04C11DB7U, 0xFFFFFFFFU, true, true, 0xFFFFFFFFU};
    static const wf_crc_params ccitt = {16U, 0x1021U, 0xFFFFU, false, false, 0U};
    static const wf_crc_params modbus = {16U, 0x8005U, 0xFFFFU, true, true, 0U};
    static const wf_crc_params dvb = {8U, 0xD5U, 0U, false, false, 0U};
    static const wf_crc_params crc64 = {64U, 0x42F0E1EBA9EA3693ULL, 0U, false, false, 0U};
    static const wf_crc_params crc3 = {3U, 0x3U, 0x7U, true, true, 0U};
    CHECK(wf_crc(&crc32, check, sizeof(check)) == 0xCBF43926U);
    CHECK(wf_crc(&ccitt, check, sizeof(check)) == 0x29B1U);
    CHECK(wf_crc(&modbus, check, sizeof(check)) == 0x4B37U);
    CHECK(wf_crc(&dvb, check, sizeof(check)) == 0xBCU);
    CHECK(wf_crc(&crc64, check, sizeof(check)) == 0x6C40DF5F0B497347ULL);
    CHECK(wf_crc(&crc3, check, sizeof(check)) == 0x6U);
    CHECK(wf_crc(&crc32, check, 0U) == 0U);
}

int main(void) {
    test_status();
    test_masks_and_fits();
    test_packer();
    test_unpacker();
    test_store_and_load();
    test_writer();
    test_reader();
    test_encoder();
    test_decoder();
    test_decoder_text_and_counts();
    test_decoder_bit_groups();
    test_decoder_checksum();
    test_validation();
    test_expressions();
    test_crc();
    if (failures == 0) {
        printf("c runtime: all checks passed\n");
    }
    return failures == 0 ? 0 : 1;
}
