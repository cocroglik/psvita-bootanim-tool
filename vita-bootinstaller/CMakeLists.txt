cmake_minimum_required(VERSION 3.10)
project(bootinstaller)

set(CMAKE_TOOLCHAIN_FILE "$ENV{VITASDK}/share/vita.toolchain.cmake" CACHE FILEPATH "VitaSDK toolchain")
include($ENV{VITASDK}/share/vita.cmake)

add_executable(${PROJECT_NAME}.elf
    src/main.c
)

target_link_libraries(${PROJECT_NAME}.elf
    vita2d
    psp2_types
    psp2_kernel
    psp2_io
    psp2_ctrl
    psp2_display
    psp2_power
    psp2_sysmodule
    psp2_apputil
    SceLibKernel_stub
    SceLibc_stub
    SceIofilemgr_stub
    SceCtrl_stub
    ScePower_stub
    SceSysmodule_stub
    SceAppUtil_stub
)
