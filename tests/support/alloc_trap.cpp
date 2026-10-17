#include "support/alloc_trap.hpp"

#include <atomic>
#include <cstdlib>
#include <new>

namespace {

struct Counters {
    std::atomic<std::size_t> allocations{0U};
};

Counters& counters() noexcept {
    static Counters instance;
    return instance;
}

void* allocate_nothrow(std::size_t size) noexcept {
    counters().allocations.fetch_add(1U, std::memory_order_relaxed);
    return std::malloc(size == 0U ? 1U : size);
}

void* allocate(std::size_t size) {
    void* memory = allocate_nothrow(size);
    if (memory == nullptr) {
        throw std::bad_alloc();
    }
    return memory;
}

void release(void* memory) noexcept {
    std::free(memory);
}

} // namespace

// Every non-over-aligned allocation form must be replaced together, otherwise sanitizers report
// alloc/dealloc mismatches between the default and the replaced forms.
void* operator new(std::size_t size) {
    return allocate(size);
}
void* operator new[](std::size_t size) {
    return allocate(size);
}
void* operator new(std::size_t size, const std::nothrow_t& /*tag*/) noexcept {
    return allocate_nothrow(size);
}
void* operator new[](std::size_t size, const std::nothrow_t& /*tag*/) noexcept {
    return allocate_nothrow(size);
}
void operator delete(void* memory) noexcept {
    release(memory);
}
void operator delete[](void* memory) noexcept {
    release(memory);
}
void operator delete(void* memory, std::size_t /*size*/) noexcept {
    release(memory);
}
void operator delete[](void* memory, std::size_t /*size*/) noexcept {
    release(memory);
}
void operator delete(void* memory, const std::nothrow_t& /*tag*/) noexcept {
    release(memory);
}
void operator delete[](void* memory, const std::nothrow_t& /*tag*/) noexcept {
    release(memory);
}

namespace wireforge::testing {

AllocationTrap::AllocationTrap() noexcept
    : baseline_(counters().allocations.load(std::memory_order_relaxed)) {}

std::size_t AllocationTrap::allocations() const noexcept {
    return counters().allocations.load(std::memory_order_relaxed) - baseline_;
}

} // namespace wireforge::testing
