/**
 * @file ring.h
 * @brief A byte ring over caller-provided storage.
 *
 * Not thread safe: the producer and the consumer of one ring must not run at the same time unless
 * the application guards the calls (for example with a critical section around the writes of an
 * interrupt handler).
 */
#ifndef WIREFORGE_C_RING_H
#define WIREFORGE_C_RING_H

#include <stddef.h>
#include <stdint.h>

/// A byte ring; the whole storage is usable.
typedef struct wf_ring {
    uint8_t* storage; ///< The memory of the ring.
    size_t capacity;  ///< Size of the storage.
    size_t head;      ///< Index of the oldest byte.
    size_t size;      ///< Bytes stored.
} wf_ring;

/// Uses @p storage of @p capacity bytes as the memory of the ring; it must outlive the ring.
static inline void wf_ring_init(wf_ring* ring, uint8_t* storage, size_t capacity) {
    ring->storage = storage;
    ring->capacity = capacity;
    ring->head = 0U;
    ring->size = 0U;
}

/// Bytes the producer can still write.
static inline size_t wf_ring_free_space(const wf_ring* ring) {
    return ring->capacity - ring->size;
}

/// Empties the ring.
static inline void wf_ring_reset(wf_ring* ring) {
    ring->head = 0U;
    ring->size = 0U;
}

/// Appends as many bytes of @p bytes as fit; returns the number stored.
static inline size_t wf_ring_write(wf_ring* ring, const uint8_t* bytes, size_t length) {
    const size_t space = wf_ring_free_space(ring);
    const size_t count = length < space ? length : space;
    size_t slot = (ring->head + ring->size) % (ring->capacity == 0U ? 1U : ring->capacity);
    for (size_t index = 0U; index < count; ++index) {
        ring->storage[slot] = bytes[index];
        slot = slot + 1U == ring->capacity ? 0U : slot + 1U;
    }
    ring->size += count;
    return count;
}

/**
 * Copies bytes without consuming them.
 * @param out Destination; up to @p out_size bytes are copied.
 * @param offset Number of stored bytes to skip before copying.
 * @return The number of bytes copied.
 */
static inline size_t wf_ring_peek(const wf_ring* ring, uint8_t* out, size_t out_size,
                                  size_t offset) {
    if (offset >= ring->size) {
        return 0U;
    }
    const size_t available = ring->size - offset;
    const size_t count = out_size < available ? out_size : available;
    size_t slot = (ring->head + offset) % ring->capacity;
    for (size_t index = 0U; index < count; ++index) {
        out[index] = ring->storage[slot];
        slot = slot + 1U == ring->capacity ? 0U : slot + 1U;
    }
    return count;
}

/// Drops up to @p count of the oldest bytes; returns the number dropped.
static inline size_t wf_ring_consume(wf_ring* ring, size_t count) {
    const size_t dropped = count < ring->size ? count : ring->size;
    ring->head = (ring->head + dropped) % (ring->capacity == 0U ? 1U : ring->capacity);
    ring->size -= dropped;
    return dropped;
}

/// Copies and consumes up to @p out_size bytes; returns the number read.
static inline size_t wf_ring_read(wf_ring* ring, uint8_t* out, size_t out_size) {
    const size_t count = wf_ring_peek(ring, out, out_size, 0U);
    return wf_ring_consume(ring, count);
}

#endif /* WIREFORGE_C_RING_H */
