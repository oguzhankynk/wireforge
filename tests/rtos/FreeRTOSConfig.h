// FreeRTOS configuration of the test harness: Cortex-M4F, preemptive, static and dynamic allocation.
#ifndef WIREFORGE_RTOS_FREERTOS_CONFIG_H
#define WIREFORGE_RTOS_FREERTOS_CONFIG_H

#include <stdint.h>

#define configUSE_PREEMPTION 1
#define configUSE_TIME_SLICING 1
#define configUSE_PORT_OPTIMISED_TASK_SELECTION 0
#define configCPU_CLOCK_HZ 25000000UL
#define configTICK_RATE_HZ 1000U
#define configMAX_PRIORITIES 5U
#define configMINIMAL_STACK_SIZE 256U
#define configTOTAL_HEAP_SIZE (512U * 1024U)
#define configMAX_TASK_NAME_LEN 12U
#define configUSE_16_BIT_TICKS 0
#define configIDLE_SHOULD_YIELD 1
#define configUSE_MUTEXES 1
#define configUSE_RECURSIVE_MUTEXES 0
#define configUSE_COUNTING_SEMAPHORES 1
#define configQUEUE_REGISTRY_SIZE 0U
#define configUSE_QUEUE_SETS 0
#define configUSE_TASK_NOTIFICATIONS 1
#define configSUPPORT_STATIC_ALLOCATION 1
#define configSUPPORT_DYNAMIC_ALLOCATION 1
#define configUSE_IDLE_HOOK 0
#define configUSE_TICK_HOOK 0
#define configUSE_MALLOC_FAILED_HOOK 0
#define configCHECK_FOR_STACK_OVERFLOW 0
#define configUSE_TRACE_FACILITY 0
#define configUSE_STATS_FORMATTING_FUNCTIONS 0
#define configUSE_CO_ROUTINES 0
#define configUSE_TIMERS 0
#define configUSE_NEWLIB_REENTRANT 0

#define configKERNEL_INTERRUPT_PRIORITY (15U << 4U)
#define configMAX_SYSCALL_INTERRUPT_PRIORITY (5U << 4U)
#define configPRIO_BITS 4U

#define INCLUDE_vTaskDelete 1
#define INCLUDE_vTaskDelay 1
#define INCLUDE_vTaskSuspend 1
#define INCLUDE_xTaskGetSchedulerState 1
#define INCLUDE_xTaskGetCurrentTaskHandle 1
#define INCLUDE_uxTaskGetStackHighWaterMark 1

#define vPortSVCHandler SVC_Handler
#define xPortPendSVHandler PendSV_Handler
#define xPortSysTickHandler SysTick_Handler

#endif
