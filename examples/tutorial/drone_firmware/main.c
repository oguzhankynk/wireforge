/* The vehicle of the WireForge tutorial as firmware: the generated C code and vehicle.c on a
 * Cortex-M4 (QEMU's mps2-an386 board), MAVLink 2 over the board's first UART. No operating system:
 * a loop paced by the SysTick timer runs the vehicle every 10 ms. Nothing allocates memory. */
#include "semihosting.h"
#include "vehicle.h"

#include <stdint.h>

/* CMSDK APB UART0 of the board. */
#define UART0_BASE 0x40004000U
#define UART_DATA (*(volatile uint32_t*)(UART0_BASE + 0x00U))
#define UART_STATE (*(volatile uint32_t*)(UART0_BASE + 0x04U))
#define UART_CTRL (*(volatile uint32_t*)(UART0_BASE + 0x08U))
#define UART_BAUDDIV (*(volatile uint32_t*)(UART0_BASE + 0x10U))
#define UART_TX_FULL 0x1U
#define UART_RX_FULL 0x2U

/* SysTick of the Cortex-M4; the board runs at 25 MHz. */
#define SYST_CSR (*(volatile uint32_t*)0xE000E010U)
#define SYST_RVR (*(volatile uint32_t*)0xE000E014U)
#define SYST_CVR (*(volatile uint32_t*)0xE000E018U)
#define SYST_COUNTFLAG 0x10000U
#define CPU_HZ 25000000U

/* The transport: bytes go to the UART as long as it takes them; received bytes are read until
 * the UART is empty. Neither function waits. */
static size_t uart_send(void* context, const uint8_t* bytes, size_t length) {
    size_t sent = 0U;
    (void)context;
    while (sent < length) {
        while ((UART_STATE & UART_TX_FULL) != 0U) {
            /* a frame is short; wait for room rather than drop the rest */
        }
        UART_DATA = bytes[sent++];
    }
    return sent;
}

static size_t uart_receive(void* context, uint8_t* buffer, size_t capacity) {
    size_t got = 0U;
    (void)context;
    while (got < capacity && (UART_STATE & UART_RX_FULL) != 0U) {
        buffer[got++] = (uint8_t)UART_DATA;
    }
    return got;
}

int main(void) {
    static vehicle drone; /* the link buffers live in .bss */
    const wf_transport transport = {NULL, uart_send, uart_receive};
    UART_BAUDDIV = 16U;
    UART_CTRL = 0x3U; /* transmit and receive */
    SYST_RVR = CPU_HZ / 100U - 1U;
    SYST_CVR = 0U;
    SYST_CSR = 0x5U; /* enable, processor clock, no interrupt */
    vehicle_init(&drone, &transport, semihosting_print);
    semihosting_print("firmware: MAVLink 2 on UART0\n");
    for (;;) {
        while ((SYST_CSR & SYST_COUNTFLAG) == 0U) {
        }
        vehicle_tick(&drone);
    }
}
