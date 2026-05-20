cmake_minimum_required(VERSION 3.10)
project(bootinstaller)

set(CMAKE_TOOLCHAIN_FILE "$ENV{VITASDK}/share/vita.toolchain.cmake" CACHE FILEPATH "VitaSDK toolchain")
include($ENV{VITASDK}/share/vita.cmake)

add_executable(${PROJECT_NAME}.elf
    src/main.c
)

target_link_libraries(${PROJECT_NAME}.elf
    vita2d
    SceLibKernel_stub
    SceLibc_stub
    SceIofilemgr_stub
    SceCtrl_stub
    ScePower_stub
    SceSysmodule_stub
    SceAppUtil_stub
    SceDisplay_stub
)
