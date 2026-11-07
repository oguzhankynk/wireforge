# The gimbal firmware's code: only the unit `gimbal` of the system model, in C.
cmake_minimum_required(VERSION 3.21)
project(gimbal_app LANGUAGES C)

find_package(WireForge REQUIRED)
wireforge_generate(gimbal_model PROJECT "${SYSTEM_PROJECT}" NODE gimbal)

add_executable(gimbal main.c)
target_link_libraries(gimbal PRIVATE gimbal_model)
