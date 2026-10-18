// Start-up of the Cortex-M4 on QEMU mps2-an386: vector table, data and bss, C library start.
#include "semihosting.hpp"

#include <cstdint>

extern "C" {

extern std::uint32_t _data_load;
extern std::uint32_t _data_start;
extern std::uint32_t _data_end;
extern std::uint32_t _bss_start;
extern std::uint32_t _bss_end;
extern std::uint32_t _stack_top;

void __libc_init_array(void);
int main(void);

void Reset_Handler(void);
void SVC_Handler(void);
void PendSV_Handler(void);
void SysTick_Handler(void);

// Any fault ends the run with a failure instead of hanging.
void Fault_Handler(void) {
    rtos::exit_with(2U);
}

// The libraries print through newlib: every byte goes to the semihosting console.
int _write(int /*file*/, const char* data, int length) {
    for (int index = 0; index < length; ++index) {
        rtos::write_char(data[index]);
    }
    return length;
}

using Handler = void (*)(void);

// The vector table is a plain array of addresses placed by the linker script.
__attribute__((section(".vectors"), used)) const Handler vectors[] = {
    reinterpret_cast<Handler>(&_stack_top), // initial stack pointer
    Reset_Handler,
    Fault_Handler, // NMI
    Fault_Handler, // HardFault
    Fault_Handler, // MemManage
    Fault_Handler, // BusFault
    Fault_Handler, // UsageFault
    nullptr,
    nullptr,
    nullptr,
    nullptr,
    SVC_Handler,
    Fault_Handler, // DebugMon
    nullptr,
    PendSV_Handler,
    SysTick_Handler,
};

void Reset_Handler(void) {
    const std::uint32_t* source = &_data_load;
    for (std::uint32_t* target = &_data_start; target < &_data_end; ++target) {
        *target = *source++;
    }
    for (std::uint32_t* target = &_bss_start; target < &_bss_end; ++target) {
        *target = 0U;
    }
    __libc_init_array();
    rtos::exit_with(static_cast<std::uint32_t>(main()));
}

} // extern "C"
