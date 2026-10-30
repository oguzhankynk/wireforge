/* The vehicle of the WireForge tutorial as firmware on a real board: the generated C code and
 * vehicle.c on an STM32H743 (NUCLEO-H743ZI2), MAVLink 2 over USART3, which the board's ST-LINK
 * offers to the computer as a serial port. No operating system and no vendor library: a loop moves
 * bytes between the UART and two ring buffers, and SysTick paces the vehicle every 10 ms. The green
 * LED blinks while it runs; the yellow LED is on while the vehicle is armed. */
#include "vehicle.h"
#include "wireforge/c/ring.h"

#include <stdint.h>

#define REG(address) (*(volatile uint32_t*)(address))

#define RCC_AHB4ENR REG(0x580244E0U)
#define RCC_APB1LENR REG(0x580244E8U)
#define RCC_GPIOB 0x2U
#define RCC_GPIOD 0x8U
#define RCC_GPIOE 0x10U
#define RCC_USART3 (1U << 18U)

#define GPIO_MODER(base) REG((base) + 0x00U)
#define GPIO_BSRR(base) REG((base) + 0x18U)
#define GPIO_AFRH(base) REG((base) + 0x24U)
#define GPIOB 0x58020400U
#define GPIOD 0x58020C00U
#define GPIOE 0x58021000U
#define GREEN_LED 0U  /* PB0, LD1 */
#define YELLOW_LED 1U /* PE1, LD2 */

#define USART_CR1 REG(0x40004800U)
#define USART_BRR REG(0x4000480CU)
#define USART_ISR REG(0x4000481CU)
#define USART_ICR REG(0x40004820U)
#define USART_RDR REG(0x40004824U)
#define USART_TDR REG(0x40004828U)
#define USART_ENABLE 0xDU      /* UE, RE, TE */
#define USART_FIFO (1U << 29U) /* 16 bytes each way */
#define USART_RX_NOT_EMPTY 0x20U
#define USART_TX_NOT_FULL 0x80U
#define USART_OVERRUN 0x8U

/* SysTick of the Cortex-M7. After reset the chip runs on its internal 64 MHz oscillator. */
#define SYST_CSR REG(0xE000E010U)
#define SYST_RVR REG(0xE000E014U)
#define SYST_CVR REG(0xE000E018U)
#define SYST_COUNTFLAG 0x10000U
#define CPU_HZ 64000000U
#define BAUD 115200U

#define TICKS_PER_BLINK 50U
#define RING_BYTES 1024U

static wf_ring tx_ring;
static wf_ring rx_ring;

/* The transport: a UART sends one byte in 87 us, so the link only fills and empties the rings. */
static size_t uart_send(void* context, const uint8_t* bytes, size_t length) {
    (void)context;
    return wf_ring_write(&tx_ring, bytes, length);
}

static size_t uart_receive(void* context, uint8_t* buffer, size_t capacity) {
    (void)context;
    return wf_ring_read(&rx_ring, buffer, capacity);
}

/* Moves what the UART can take or has received; called all the time between the ticks. */
static void uart_pump(void) {
    uint8_t byte = 0U;
    while ((USART_ISR & USART_RX_NOT_EMPTY) != 0U) {
        byte = (uint8_t)USART_RDR;
        (void)wf_ring_write(&rx_ring, &byte, 1U);
    }
    USART_ICR = USART_OVERRUN; /* otherwise an overrun stops reception */
    while ((USART_ISR & USART_TX_NOT_FULL) != 0U && wf_ring_read(&tx_ring, &byte, 1U) == 1U) {
        USART_TDR = byte;
    }
}

static void board_init(void) {
    RCC_AHB4ENR |= RCC_GPIOB | RCC_GPIOD | RCC_GPIOE;
    RCC_APB1LENR |= RCC_USART3;
    (void)RCC_APB1LENR; /* the clock needs a moment before the first access */
    GPIO_MODER(GPIOB) = (GPIO_MODER(GPIOB) & ~0x3U) | 0x1U;                   /* PB0: output */
    GPIO_MODER(GPIOE) = (GPIO_MODER(GPIOE) & ~0xCU) | 0x4U;                   /* PE1: output */
    GPIO_AFRH(GPIOD) = (GPIO_AFRH(GPIOD) & ~0xFFU) | 0x77U;                   /* PD8, PD9: AF7 */
    GPIO_MODER(GPIOD) = (GPIO_MODER(GPIOD) & ~(0xFU << 16U)) | (0xAU << 16U); /* alternate */
    USART_BRR = CPU_HZ / BAUD;
    USART_CR1 = USART_FIFO | USART_ENABLE;
    SYST_RVR = CPU_HZ / 100U - 1U;
    SYST_CVR = 0U;
    SYST_CSR = 0x5U; /* enable, processor clock, no interrupt */
}

static void led(uint32_t port, uint32_t pin, bool on) {
    GPIO_BSRR(port) = 1U << (on ? pin : pin + 16U);
}

static void no_log(const char* text) {
    (void)text; /* the only serial port carries MAVLink */
}

int main(void) {
    static vehicle drone; /* the link buffers live in .bss */
    static uint8_t tx_storage[RING_BYTES];
    static uint8_t rx_storage[RING_BYTES];
    const wf_transport transport = {NULL, uart_send, uart_receive};
    uint32_t ticks = 0U;
    wf_ring_init(&tx_ring, tx_storage, sizeof tx_storage);
    wf_ring_init(&rx_ring, rx_storage, sizeof rx_storage);
    board_init();
    vehicle_init(&drone, &transport, no_log);
    for (;;) {
        while ((SYST_CSR & SYST_COUNTFLAG) == 0U) {
            uart_pump();
        }
        vehicle_tick(&drone);
        ++ticks;
        led(GPIOB, GREEN_LED, (ticks / TICKS_PER_BLINK) % 2U == 0U);
        led(GPIOE, YELLOW_LED, drone.armed);
    }
}
