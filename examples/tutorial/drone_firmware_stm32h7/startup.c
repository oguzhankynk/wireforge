/* Start-up of the Cortex-M7 of the STM32H743: the vector table, .data and .bss, then main(). The
 * floating-point unit is switched on first. A fault stops here, where a debugger finds it. */
#include <stdint.h>

extern uint32_t _data_load;
extern uint32_t _data_start;
extern uint32_t _data_end;
extern uint32_t _bss_start;
extern uint32_t _bss_end;
extern uint32_t _stack_top;

int main(void);
void Reset_Handler(void);
void Fault_Handler(void);

void Fault_Handler(void) {
    for (;;) {
    }
}

typedef void (*handler)(void);

/* The vector table: the initial stack pointer, then the exception handlers. */
__attribute__((section(".vectors"), used)) static const handler vectors[16] = {
    (handler)(uintptr_t)&_stack_top,
    Reset_Handler,
    Fault_Handler, /* NMI */
    Fault_Handler, /* HardFault */
    Fault_Handler, /* MemManage */
    Fault_Handler, /* BusFault */
    Fault_Handler, /* UsageFault */
};

void Reset_Handler(void) {
    /* The floating-point unit is off after reset; the code is built for it (hard float). */
    *(volatile uint32_t*)0xE000ED88U |= 0xFU << 20U; /* CPACR: full access to CP10 and CP11 */
    __asm__ volatile("dsb\n"
                     "isb\n");
    const uint32_t* source = &_data_load;
    for (uint32_t* target = &_data_start; target < &_data_end; ++target) {
        *target = *source++;
    }
    for (uint32_t* target = &_bss_start; target < &_bss_end; ++target) {
        *target = 0U;
    }
    (void)main();
    Fault_Handler();
}
