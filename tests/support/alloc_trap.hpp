#ifndef WIREFORGE_TESTS_SUPPORT_ALLOC_TRAP_HPP
#define WIREFORGE_TESTS_SUPPORT_ALLOC_TRAP_HPP

#include <cstddef>

namespace wireforge::testing {

/// Counts global heap allocations (operator new) made while an instance is alive.
/// Used to prove that runtime and generated code never touch the heap.
/// Limitation: over-aligned allocations bypass the counter; runtime types must not need them.
class AllocationTrap {
public:
    AllocationTrap() noexcept;
    ~AllocationTrap() = default;
    AllocationTrap(const AllocationTrap&) = delete;
    AllocationTrap& operator=(const AllocationTrap&) = delete;
    AllocationTrap(AllocationTrap&&) = delete;
    AllocationTrap& operator=(AllocationTrap&&) = delete;

    /// Number of allocations observed since construction.
    [[nodiscard]] std::size_t allocations() const noexcept;

private:
    std::size_t baseline_;
};

} // namespace wireforge::testing

#endif // WIREFORGE_TESTS_SUPPORT_ALLOC_TRAP_HPP
