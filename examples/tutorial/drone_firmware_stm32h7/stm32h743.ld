/* Memory map of the STM32H743: flash at 0x08000000, the 128 KB of DTCM RAM at 0x20000000. */
ENTRY(Reset_Handler)

MEMORY
{
    CODE (rx) : ORIGIN = 0x08000000, LENGTH = 2M
    DATA (rwx) : ORIGIN = 0x20000000, LENGTH = 128K
}

_stack_size = 8K;
_libc_heap_size = 4K; /* the firmware allocates nothing; a little room in case the C library asks */

SECTIONS
{
    .vectors : { KEEP(*(.vectors)) } > CODE

    .text :
    {
        *(.text*)
        KEEP(*(.init))
        KEEP(*(.fini))
        *(.rodata*)
        . = ALIGN(4);
        __init_array_start = .;
        KEEP(*(SORT(.init_array.*)))
        KEEP(*(.init_array))
        __init_array_end = .;
        . = ALIGN(4);
    } > CODE

    .ARM.extab : { *(.ARM.extab* .gnu.linkonce.armextab.*) } > CODE
    .ARM.exidx : { *(.ARM.exidx* .gnu.linkonce.armexidx.*) } > CODE

    _data_load = LOADADDR(.data);
    .data : { _data_start = .; *(.data*) . = ALIGN(4); _data_end = .; } > DATA AT> CODE

    .bss : { _bss_start = .; *(.bss*) *(COMMON) . = ALIGN(4); _bss_end = .; } > DATA

    .heap (NOLOAD) : { . = ALIGN(8); end = .; _heap_start = .; . += _libc_heap_size; } > DATA

    .stack (NOLOAD) :
    {
        . = ALIGN(8);
        _stack_bottom = .;
        . += _stack_size;
        _stack_top = .;
    } > DATA
}
