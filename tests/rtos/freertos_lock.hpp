// The lock policy of the runtime on FreeRTOS: a mutex of the kernel. It is the example for
// connecting the runtime to the locking of an RTOS (docs/RUNTIME.md); the harness makes it the
// default lock of the library with -DWIREFORGE_DEFAULT_LOCK=FreeRtosLock.
#ifndef WIREFORGE_RTOS_FREERTOS_LOCK_HPP
#define WIREFORGE_RTOS_FREERTOS_LOCK_HPP

#include "FreeRTOS.h"
#include "semphr.h"

/// @brief Lock policy backed by a statically allocated FreeRTOS mutex.
class FreeRtosLock {
public:
    FreeRtosLock() noexcept : handle_(xSemaphoreCreateMutexStatic(&storage_)) {}
    ~FreeRtosLock() = default;
    FreeRtosLock(const FreeRtosLock&) = delete;
    FreeRtosLock& operator=(const FreeRtosLock&) = delete;
    FreeRtosLock(FreeRtosLock&&) = delete;
    FreeRtosLock& operator=(FreeRtosLock&&) = delete;

    /// @brief Blocks the calling task until the mutex is taken.
    void lock() noexcept { static_cast<void>(xSemaphoreTake(handle_, portMAX_DELAY)); }
    /// @brief Gives the mutex back.
    void unlock() noexcept { static_cast<void>(xSemaphoreGive(handle_)); }

private:
    StaticSemaphore_t storage_{};
    SemaphoreHandle_t handle_;
};

#endif
