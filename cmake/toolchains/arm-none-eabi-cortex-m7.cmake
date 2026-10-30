# Bare-metal Arm Cortex-M7 with a double-precision floating-point unit, newlib-nano (STM32H7).
set(cpu_flags "-mcpu=cortex-m7 -mthumb -mfloat-abi=hard -mfpu=fpv5-d16")
set(multilib_path "thumb/v7e-m+dp/hard")
include("${CMAKE_CURRENT_LIST_DIR}/arm-none-eabi.cmake")
