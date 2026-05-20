/*
 * PS Vita Boot Animation Installer (BootAnimInstaller)
 *
 * Busca archivos de animacion (.rcf, .cbs) en ux0:data/PSVitaBootAnim/
 * y permite instalar la seleccionada como boot splash del sistema.
 *
 * Compilacion:
 *   mkdir build && cd build
 *   cmake .. -DCMAKE_TOOLCHAIN_FILE=$VITASDK/share/vita.toolchain.cmake
 *   make
 *   make install DESTDIR=./out && vita-makepkg -s -f out/
 */

#include <psp2/kernel/processmgr.h>
#include <psp2/kernel/threadmgr.h>
#include <psp2/io/dirent.h>
#include <psp2/io/stat.h>
#include <psp2/io/fcntl.h>
#include <psp2/ctrl.h>
#include <psp2/display.h>
#include <psp2/power.h>
#include <psp2/sysmodule.h>
#include <psp2/apputil.h>
#include <vita2d.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>

#define RCF_DIR   "ux0:data/PSVitaBootAnim/"
#define TARGET_UR0   "ur0:tai/boot_splash.rcf"
#define TARGET_CBS   "ux0:data/PSP2CBS/custom1.cbs"
#define CBS_DIR      "ux0:data/PSP2CBS/"

#define MAX_ENTRIES 128
#define MAX_NAME    256
#define MAX_PATH    512

typedef struct {
    char name[MAX_NAME];
    char path[MAX_PATH];
    int  is_rcf;
} AnimEntry;

static AnimEntry entries[MAX_ENTRIES];
static int entry_count = 0;
static int cursor = 0;
static int scroll_offset = 0;
static int install_mode = 0; // 0=menu, 1=confirm, 2=installing, 3=done, 4=error
static int install_target = 0; // 0=Enso Ex (RCF), 1=CBS Manager
static int menu_page = 0; // 0=file list, 1=select target
static char status_text[256];
static vita2d_font *font = NULL;

static void log_msg(const char *fmt, ...) {
    va_list args;
    va_start(args, fmt);
    vsnprintf(status_text, sizeof(status_text), fmt, args);
    va_end(args);
}

static int cmp_name(const void *a, const void *b) {
    const AnimEntry *ea = (const AnimEntry *)a;
    const AnimEntry *eb = (const AnimEntry *)b;
    return strcasecmp(ea->name, eb->name);
}

static void scan_files(void) {
    entry_count = 0;
    SceUID dfd = sceIoDopen(RCF_DIR);
    if (dfd < 0) {
        log_msg("No se pudo abrir %s (error 0x%08X)", RCF_DIR, dfd);
        return;
    }
    SceIoDirent dirent;
    memset(&dirent, 0, sizeof(dirent));
    while (sceIoDread(dfd, &dirent) > 0 && entry_count < MAX_ENTRIES) {
        if (SCE_S_ISDIR(dirent.d_stat.st_mode))
            continue;
        const char *name = dirent.d_name;
        int len = strlen(name);
        if (len < 4) continue;
        const char *ext = name + len - 4;
        int is_rcf = (strcasecmp(ext, ".rcf") == 0 || strcasecmp(ext, ".cbs") == 0);
        if (!is_rcf && strcasecmp(ext, ".bin") != 0)
            continue;
        strncpy(entries[entry_count].name, name, MAX_NAME - 1);
        snprintf(entries[entry_count].path, MAX_PATH, "%s%s", RCF_DIR, name);
        entries[entry_count].is_rcf = (strcasecmp(ext, ".rcf") == 0);
        entry_count++;
    }
    sceIoDclose(dfd);
    if (entry_count > 1)
        qsort(entries, entry_count, sizeof(AnimEntry), cmp_name);
    log_msg("Encontrados %d archivos en %s", entry_count, RCF_DIR);
}

static int copy_file(const char *src, const char *dst) {
    // Ensure parent dir exists
    char dir[MAX_PATH];
    strncpy(dir, dst, MAX_PATH);
    char *p = strrchr(dir, '/');
    if (p) {
        *p = '\0';
        sceIoMkdir(dir, 0777);
        *p = '/';
    }
    int fds = sceIoOpen(src, SCE_O_RDONLY, 0);
    if (fds < 0) return fds;
    int fdd = sceIoOpen(dst, SCE_O_WRONLY | SCE_O_CREAT | SCE_O_TRUNC, 0777);
    if (fdd < 0) {
        sceIoClose(fds);
        return fdd;
    }
    char buf[65536];
    int ret = 0;
    while (1) {
        int r = sceIoRead(fds, buf, sizeof(buf));
        if (r <= 0) break;
        int w = sceIoWrite(fdd, buf, r);
        if (w != r) { ret = -1; break; }
    }
    sceIoClose(fds);
    sceIoClose(fdd);
    return ret;
}

static void install_animation(int idx, int target) {
    if (idx < 0 || idx >= entry_count) return;
    const char *src = entries[idx].path;
    const char *dst = target == 0 ? TARGET_UR0 : TARGET_CBS;
    log_msg("Instalando %s ...", entries[idx].name);
    int ret = copy_file(src, dst);
    if (ret == 0) {
        log_msg("Instalado correctamente en %s", dst);
        install_mode = 3;
    } else {
        log_msg("Error al instalar (ret=0x%08X)", ret);
        install_mode = 4;
    }
}

static void draw_menu(void) {
    vita2d_start_drawing();
    vita2d_clear_screen();
    vita2d_draw_rectangle(0, 0, 960, 60, RGBA8(20, 80, 160, 255));
    if (font) {
        vita2d_font_draw_text(font, 20, 40, RGBA8(255, 255, 255, 255), 28, "PS Vita Boot Anim Installer");
        vita2d_font_draw_text(font, 20, 90, RGBA8(200, 200, 200, 255), 18, "D-Pad: Navegar  X: Seleccionar  O: Volver");
    }
    // File list
    int y_start = 110;
    int y_step = 24;
    int visible = (544 - y_start - 40) / y_step;
    if (cursor < scroll_offset)
        scroll_offset = cursor;
    if (cursor >= scroll_offset + visible)
        scroll_offset = cursor - visible + 1;
    if (scroll_offset > entry_count - visible)
        scroll_offset = entry_count - visible;
    if (scroll_offset < 0) scroll_offset = 0;
    for (int i = scroll_offset; i < entry_count && i < scroll_offset + visible; i++) {
        int y = y_start + (i - scroll_offset) * y_step;
        int is_cursor = (i == cursor);
        if (is_cursor)
            vita2d_draw_rectangle(10, y - 4, 940, y_step, RGBA8(60, 120, 200, 120));
        unsigned int color = is_cursor ? RGBA8(255, 255, 100, 255) : RGBA8(200, 200, 200, 255);
        const char *tag = entries[i].is_rcf ? "RCF" : "CBS";
        char line[MAX_NAME + 16];
        snprintf(line, sizeof(line), "[%s] %s", tag, entries[i].name);
        if (font)
            vita2d_font_draw_text(font, 20, y, color, 18, line);
        else
            vita2d_font_draw_text(font, 20, y, color, 18, line);
    }
    // Status bar
    vita2d_draw_rectangle(0, 544 - 30, 960, 30, RGBA8(10, 10, 10, 200));
    if (font)
        vita2d_font_draw_text(font, 20, 544 - 22, RGBA8(150, 200, 255, 255), 16, status_text);
    else
        vita2d_font_draw_text(font, 20, 544 - 22, RGBA8(150, 200, 255, 255), 16, status_text);
    vita2d_end_drawing();
    vita2d_swap_buffers();
}

static void draw_confirm(void) {
    vita2d_start_drawing();
    vita2d_clear_screen();
    // Title
    vita2d_draw_rectangle(0, 0, 960, 60, RGBA8(20, 80, 160, 255));
    if (font) {
        vita2d_font_draw_text(font, 20, 40, RGBA8(255, 255, 255, 255), 28, "Confirmar instalacion");
    }
    // Panel de confirmacion
    vita2d_draw_rectangle(80, 120, 800, 300, RGBA8(30, 30, 50, 240));
    // Info del archivo seleccionado
    char line1[MAX_PATH + 32];
    snprintf(line1, sizeof(line1), "Archivo: %s", entries[cursor].name);
    if (font)
        vita2d_font_draw_text(font, 120, 170, RGBA8(255, 255, 255, 255), 22, line1);
    // Destino
    const char *target_name = install_target == 0 ? "Enso Ex (ur0:tai/boot_splash.rcf)" : "CBS Manager (ux0:data/PSP2CBS/custom1.cbs)";
    if (font)
        vita2d_font_draw_text(font, 120, 210, RGBA8(200, 200, 200, 255), 18, target_name);
    // Opciones
    if (font) {
        vita2d_font_draw_text(font, 200, 280, RGBA8(100, 255, 100, 255), 22, "X = Instalar");
        vita2d_font_draw_text(font, 200, 320, RGBA8(255, 150, 150, 255), 22, "O = Cancelar");
    }
    vita2d_draw_rectangle(0, 544 - 30, 960, 30, RGBA8(10, 10, 10, 200));
    if (font)
        vita2d_font_draw_text(font, 20, 544 - 22, RGBA8(150, 200, 255, 255), 16, status_text);
    vita2d_end_drawing();
    vita2d_swap_buffers();
}

static void draw_done(void) {
    vita2d_start_drawing();
    vita2d_clear_screen();
    vita2d_draw_rectangle(0, 0, 960, 60, RGBA8(20, 160, 80, 255));
    if (font) {
        vita2d_font_draw_text(font, 20, 40, RGBA8(255, 255, 255, 255), 28, "Instalacion completa");
        vita2d_font_draw_text(font, 80, 200, RGBA8(200, 255, 200, 255), 24, status_text);
        vita2d_font_draw_text(font, 200, 300, RGBA8(255, 255, 255, 255), 20, "PS button para reiniciar");
        vita2d_font_draw_text(font, 220, 340, RGBA8(200, 200, 200, 255), 18, "O = Volver al menu");
    }
    vita2d_end_drawing();
    vita2d_swap_buffers();
}

static void draw_error(void) {
    vita2d_start_drawing();
    vita2d_clear_screen();
    vita2d_draw_rectangle(0, 0, 960, 60, RGBA8(160, 20, 20, 255));
    if (font) {
        vita2d_font_draw_text(font, 20, 40, RGBA8(255, 255, 255, 255), 28, "Error de instalacion");
        vita2d_font_draw_text(font, 80, 200, RGBA8(255, 180, 180, 255), 22, status_text);
        vita2d_font_draw_text(font, 220, 340, RGBA8(200, 200, 200, 255), 18, "O = Volver al menu");
    }
    vita2d_end_drawing();
    vita2d_swap_buffers();
}

int main(int argc, char *argv[]) {
    scePowerSetArmClockFrequency(444);
    sceSysmoduleLoadModule(SCE_SYSMODULE_APPUTIL);
    vita2d_init();
    vita2d_set_clear_color(RGBA8(15, 15, 25, 255));
    font = vita2d_load_default_pvf();
    if (!font) {
        font = vita2d_load_default_pvf();
    }
    scan_files();
    log_msg("Usa D-Pad para navegar, X para instalar");
    SceCtrlData pad;
    memset(&pad, 0, sizeof(pad));
    int old_buttons = 0;
    while (1) {
        sceCtrlPeekBufferPositive(0, &pad, 1);
        int buttons = pad.buttons;
        int pressed = buttons & ~old_buttons;
        old_buttons = buttons;
        if (install_mode == 0) {
            // Menu - file list
            if (pressed & SCE_CTRL_UP) {
                if (cursor > 0) cursor--;
            }
            if (pressed & SCE_CTRL_DOWN) {
                if (cursor < entry_count - 1) cursor++;
            }
            if (pressed & SCE_CTRL_LEFT) {
                cursor -= 5;
                if (cursor < 0) cursor = 0;
            }
            if (pressed & SCE_CTRL_RIGHT) {
                cursor += 5;
                if (cursor >= entry_count) cursor = entry_count - 1;
            }
            if (pressed & SCE_CTRL_CROSS) {
                if (entry_count > 0) {
                    install_mode = 1;
                    log_msg("Archivo: %s", entries[cursor].name);
                }
            }
            if (pressed & SCE_CTRL_SQUARE) {
                scan_files();
            }
            if (pressed & SCE_CTRL_TRIANGLE) {
                install_target = !install_target;
                log_msg("Destino: %s", install_target == 0 ? "Enso Ex" : "CBS Manager");
            }
            if (pressed & SCE_CTRL_START) {
                break;
            }
            draw_menu();
        } else if (install_mode == 1) {
            // Confirm
            if (pressed & SCE_CTRL_CROSS) {
                install_mode = 2;
                install_animation(cursor, install_target);
            }
            if (pressed & SCE_CTRL_CIRCLE) {
                install_mode = 0;
                log_msg("Instalacion cancelada");
            }
            draw_confirm();
        } else if (install_mode == 2) {
            // Installing - just show progress
            sceKernelDelayThread(10000);
            // The install happens synchronously so this is rarely seen
        } else if (install_mode == 3) {
            // Done
            if (pressed & SCE_CTRL_CIRCLE) {
                install_mode = 0;
                scan_files();
            }
            if (pressed & SCE_CTRL_PSBUTTON) {
                scePowerRequestColdReset();
            }
            draw_done();
        } else if (install_mode == 4) {
            // Error
            if (pressed & SCE_CTRL_CIRCLE) {
                install_mode = 0;
                scan_files();
            }
            draw_error();
        }
        sceKernelDelayThread(10000);
    }
    vita2d_fini();
    sceKernelExitProcess(0);
    return 0;
}
