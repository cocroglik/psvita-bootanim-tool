cmake_minimum_required(VERSION 3.10)
project(bootinstaller)

set(CMAKE_TOOLCHAIN_FILE "${VITASDK}/share/vita.toolchain.cmake" CACHE FILEPATH "VitaSDK toolchain")

add_executable(${PROJECT_NAME}
    src/main.c
)

target_link_libraries(${PROJECT_NAME}
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

vita_create_self(${PROJECT_NAME}.elf ${PROJECT_NAME}.self)
vita_create_vpk(${PROJECT_NAME}.vpk ${PROJECT_NAME}.self
    VERSION 1.0
    TITLE_ID BOTINST01
    TITLE "Boot Anim Installer"
    APP_VER 01.00
)

install(FILES sce_sys/icon0.png DESTINATION ${CMAKE_INSTALL_PREFIX}/sce_sys/)
install(FILES sce_sys/livearea/contents/template.xml DESTINATION ${CMAKE_INSTALL_PREFIX}/sce_sys/livearea/contents/)
install(FILES sce_sys/livearea/contents/bg0.png DESTINATION ${CMAKE_INSTALL_PREFIX}/sce_sys/livearea/contents/)
