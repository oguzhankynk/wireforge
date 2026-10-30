# 5. On a microcontroller

**What we will do:** run the C vehicle of chapter 4 as firmware on an Arm Cortex-M4, with MAVLink 2 over the
microcontroller's UART, and fly it against the same pymavlink ground station. No board is needed: QEMU emulates
Arm's MPS2 board (`mps2-an386`, a Cortex-M4 with a CMSDK UART) and connects its UART to a TCP port.

<!-- tutorial: needs arm-none-eabi-gcc qemu-system-arm -->

## The tools
The Arm GCC toolchain with newlib, and QEMU. On Debian or Ubuntu:
```sh
sudo apt install gcc-arm-none-eabi libnewlib-arm-none-eabi qemu-system-arm
```
(macOS: `brew install --cask gcc-arm-embedded` and `brew install qemu`.)

## Build the firmware
<!-- tutorial: run -->
```sh
cp -r wireforge-src/examples/tutorial/drone_firmware .
cmake -S drone_firmware -B build/drone_firmware -DDRONE_PROJECT="$PWD/drone" -DWireForge_DIR="$(wireforge cmake-dir)" \
  -DCMAKE_TOOLCHAIN_FILE="$PWD/wireforge-src/cmake/toolchains/arm-none-eabi.cmake"
cmake --build build/drone_firmware
arm-none-eabi-size build/drone_firmware/drone_firmware.elf
```
The firmware is the generated C code of `drone`, `vehicle.c` of chapter 4 unchanged, and two small files of its own:
`main.c` (the UART and the timer) and `startup.c` (what runs before `main`). It needs about 22 KB of flash and 14 KB of
RAM (`text` and `bss`). Of the flash, the generated code, the runtime and the vehicle are about 12 KB and the C
library's `snprintf` and `sinf` about 6 KB; the RAM is mostly the stack and the link buffers, sized for the largest
MAVLink frame. Nothing is allocated at run time.

## Fly it
<!-- tutorial: run -->
```sh
qemu-system-arm -M mps2-an386 -cpu cortex-m4 -display none -semihosting-config enable=on,target=native \
  -serial tcp:127.0.0.1:5760,server=on,wait=off -kernel build/drone_firmware/drone_firmware.elf &
sleep 1
python drone_app/ground_station.py tcp:127.0.0.1:5760
kill %1
```
QEMU runs the firmware and offers the board's UART on TCP port 5760; the ground station connects to it instead of
listening on UDP:
```text
firmware: MAVLink 2 on UART0
ground station: HEARTBEAT from system 1, type 2
ground station: ATTITUDE {'time_boot_ms': 2000, 'roll': 0.18..., 'pitch': -0.04..., ...}
ground station: VFR_HUD {'airspeed': 0.0, 'groundspeed': 0.0, 'heading': 20, ...}
vehicle: COMMAND_LONG 400 (param1 1), answering COMMAND_ACK
ground station: COMMAND_ACK {'command': 400, 'result': 0, ...}
ground station: all messages arrived
```
The lines of the firmware reach the terminal through semihosting, the debugger channel QEMU emulates.

## How the firmware works
The transport is the UART: bytes go out as long as the UART takes them, and received bytes are read until it is empty.
Neither function blocks on input, so the link never waits for the other side:
```c
static size_t uart_receive(void* context, uint8_t* buffer, size_t capacity) {
    size_t got = 0U;
    (void)context;
    while (got < capacity && (UART_STATE & UART_RX_FULL) != 0U) {
        buffer[got++] = (uint8_t)UART_DATA;
    }
    return got;
}
```
There is no operating system: `main` sets up the UART and the SysTick timer and calls `vehicle_tick()` every time the
timer has counted 10 ms. `startup.c` copies the initialized data, clears the rest and switches on the floating-point
unit (the code is built for it), as every Cortex-M4 start-up does.

## On a real board
The same `vehicle.c` runs on real hardware: `drone_firmware_stm32h7/` is for the NUCLEO-H743ZI2, a Cortex-M7 board with
an ST-LINK on it. Only the files around the vehicle differ: the board's USART3, which the ST-LINK offers to the computer
as a serial port, the start-up and the memory map. There is no vendor library.
<!-- tutorial: run -->
```sh
cp -r wireforge-src/examples/tutorial/drone_firmware_stm32h7 .
cmake -S drone_firmware_stm32h7 -B build/stm32h7 -DDRONE_PROJECT="$PWD/drone" -DWireForge_DIR="$(wireforge cmake-dir)" \
  -DCMAKE_TOOLCHAIN_FILE="$PWD/wireforge-src/cmake/toolchains/arm-none-eabi-cortex-m7.cmake"
cmake --build build/stm32h7
arm-none-eabi-size build/stm32h7/drone_firmware.elf
```
With the board on USB, flash it with [pyOCD](https://pyocd.io) (or STM32CubeProgrammer) and fly it. This replaces what
is on the board:
```sh
python -m pip install pyocd pyserial
pyocd flash -t stm32h743xx build/stm32h7/drone_firmware.elf
python drone_app/ground_station.py /dev/ttyACM0,115200
```
The green LED blinks, and the yellow LED comes on when the ground station arms the vehicle
([video](https://youtu.be/vMN9FbNLx7o)). The serial port is
`/dev/ttyACM0` on Linux and a `COM` port on Windows. On a real UART a byte takes 87 us, so this firmware does what the
QEMU one did not need: a loop moves bytes between the UART and two `wf_ring` buffers all the time, and the link only
reads and writes the rings. It needs about 21 KB of flash and 16 KB of RAM.

## On your board
- **The UART:** start from `drone_firmware_stm32h7/main.c` and replace the registers with your board's UART (or its HAL
  calls). With interrupts, let the receive interrupt put bytes into the ring buffer instead of `uart_pump`.
- **The clock:** `CPU_HZ` is the clock of the SysTick timer; any 10 ms tick works, from a timer interrupt as well.
- **With an RTOS:** call `vehicle_tick()` from one task every 10 ms. The C runtime has no locks, so the link belongs to
  that task; the C++ runtime has a lock policy for kernel mutexes (the FreeRTOS example is in `tests/rtos`).
- **Less memory:** the formatted printing of `on_command_long` and the sine of the simulated attitude bring in about
  6 KB of the C library; real firmware reads its sensors instead. The link buffers follow `max_frame_size` of the
  interface.

**More:** [C runtime](../RUNTIME.md#c-runtime), [generated C code](../CODEGEN.md#c99-code---language-c),
[runtime environments](../RUNTIME.md#environments).

**If something goes wrong:** `arm-none-eabi-gcc` not found: install the toolchain or put it on the `PATH`.
`Connection refused` from the ground station: QEMU is not running (look at its message) or port 5760 is taken.
QEMU stops at once with exit code 2: the firmware hit a fault; on a board of your own, check the start-up code first.

Next: [6. Tests](06_tests.md)
