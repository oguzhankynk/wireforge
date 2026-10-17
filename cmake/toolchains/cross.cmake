# Cross toolchain for the portability matrix: -DWIREFORGE_CROSS_TRIPLE=<triple> selects
# <triple>-g++ (big-endian s390x-linux-gnu, 32-bit i686-linux-gnu, ...). Test binaries run under
# qemu-<arch>[-static] through CTest's emulator, with the target libraries from the sysroot.
# WIREFORGE_CROSS_ROOT (optional) is an extracted toolchain tree (quality/fetch_cross_toolchains.sh)
# containing usr/bin/<triple>-g++-* and the sysroot usr/<triple>.
set(CMAKE_SYSTEM_NAME Linux)
if(NOT WIREFORGE_CROSS_TRIPLE)
  set(WIREFORGE_CROSS_TRIPLE "$ENV{WIREFORGE_CROSS_TRIPLE}")
endif()
if(NOT WIREFORGE_CROSS_ROOT)
  set(WIREFORGE_CROSS_ROOT "$ENV{WIREFORGE_CROSS_ROOT}")
endif()
list(APPEND CMAKE_TRY_COMPILE_PLATFORM_VARIABLES WIREFORGE_CROSS_TRIPLE WIREFORGE_CROSS_ROOT)
string(REGEX MATCH "^[^-]+" WIREFORGE_CROSS_ARCH "${WIREFORGE_CROSS_TRIPLE}")
set(CMAKE_SYSTEM_PROCESSOR ${WIREFORGE_CROSS_ARCH})

set(search_paths "")
set(target_root "/usr/${WIREFORGE_CROSS_TRIPLE}")
if(WIREFORGE_CROSS_ROOT)
  list(APPEND search_paths "${WIREFORGE_CROSS_ROOT}/usr/bin")
  set(target_root "${WIREFORGE_CROSS_ROOT}/usr/${WIREFORGE_CROSS_TRIPLE}")
  # The libc linker scripts of the cross packages name /usr/<triple>/lib/..., so the sysroot
  # is the extraction root.
  set(CMAKE_SYSROOT "${WIREFORGE_CROSS_ROOT}")
endif()
find_program(CMAKE_CXX_COMPILER NAMES ${WIREFORGE_CROSS_TRIPLE}-g++ ${WIREFORGE_CROSS_TRIPLE}-g++-11
             ${WIREFORGE_CROSS_TRIPLE}-g++-12 ${WIREFORGE_CROSS_TRIPLE}-g++-13
             HINTS ${search_paths} REQUIRED)

set(qemu_arch ${WIREFORGE_CROSS_ARCH})
if(qemu_arch MATCHES "^i[3-6]86$")
  set(qemu_arch i386)
endif()
find_program(WIREFORGE_QEMU NAMES qemu-${qemu_arch}-static qemu-${qemu_arch} HINTS ${search_paths}
             REQUIRED)
set(CMAKE_CROSSCOMPILING_EMULATOR ${WIREFORGE_QEMU} -L ${target_root})

set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
