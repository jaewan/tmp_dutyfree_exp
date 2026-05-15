#define _GNU_SOURCE
#include "pmu.h"
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <dirent.h>
#include <sys/ioctl.h>
#include <sys/syscall.h>

static long perf_event_open(struct perf_event_attr *attr, pid_t pid,
                             int cpu, int group_fd, unsigned long flags)
{
    return syscall(SYS_perf_event_open, attr, pid, cpu, group_fd, flags);
}

/* Resolve a named PMU event to its type+config pair by reading sysfs.
 * E.g., "unc_cha_core_snp.evict_one" on device "uncore_cha_0". */
static int resolve_cha_event(const char *pmu_device, const char *event_name,
                              uint32_t *type, uint64_t *config)
{
    char path[256];

    /* Read type */
    snprintf(path, sizeof(path), "/sys/bus/event_source/devices/%s/type",
             pmu_device);
    FILE *f = fopen(path, "r");
    if (!f) { perror(path); return -1; }
    if (fscanf(f, "%u", type) != 1) { fclose(f); return -1; }
    fclose(f);

    /* Resolve event name: strip the device prefix if present, then
     * look up the event config in sysfs events/ directory.
     * Event files are at:
     *   /sys/bus/event_source/devices/<pmu>/events/<event>
     * Content looks like: "event=0x01,umask=0x01"  */
    snprintf(path, sizeof(path), "/sys/bus/event_source/devices/%s/events/%s",
             pmu_device, event_name);
    f = fopen(path, "r");
    if (!f) {
        /* Try stripping "unc_cha_" prefix from event name */
        const char *short_name = event_name;
        if (strncmp(event_name, "unc_cha_", 8) == 0)
            short_name = event_name + 8;
        snprintf(path, sizeof(path),
                 "/sys/bus/event_source/devices/%s/events/%s",
                 pmu_device, short_name);
        f = fopen(path, "r");
        if (!f) {
            fprintf(stderr, "pmu: cannot find event '%s' in %s/events/\n",
                    event_name, pmu_device);
            return -1;
        }
    }

    /* Parse "event=0xNN" and optional "umask=0xNN" */
    char buf[256];
    if (!fgets(buf, sizeof(buf), f)) { fclose(f); return -1; }
    fclose(f);

    uint64_t ev = 0, umask = 0;
    char *tok = strtok(buf, ",\n");
    while (tok) {
        uint64_t val;
        if (sscanf(tok, "event=%li", (long *)&val) == 1) ev = val;
        if (sscanf(tok, "umask=%li", (long *)&val) == 1) umask = val;
        tok = strtok(NULL, ",\n");
    }
    *config = ev | (umask << 8);
    return 0;
}

int pmu_cha_open(pmu_cha_group_t *g, int socket, const char **events, int n)
{
    if (n > PMU_MAX_EVENTS) {
        fprintf(stderr, "pmu_cha_open: too many events (%d > %d)\n",
                n, PMU_MAX_EVENTS);
        return -1;
    }
    memset(g, -1, sizeof(*g));
    g->n_events = n;
    for (int i = 0; i < n; i++)
        snprintf(g->event_names[i], sizeof(g->event_names[i]), "%s", events[i]);

    /* Count CHA tiles for this socket */
    int tile = 0;
    char pmu_device[64];
    /* Socket 0: uncore_cha_0 .. uncore_cha_31
     * Socket 1: uncore_cha_32 .. uncore_cha_63 (SPR convention) */
    int base = socket * 32;  /* adjust if platform has different CHA numbering */
    for (int t = base; t < base + PMU_MAX_TILES; t++) {
        snprintf(pmu_device, sizeof(pmu_device), "uncore_cha_%d", t);
        char check[128];
        snprintf(check, sizeof(check),
                 "/sys/bus/event_source/devices/%s/type", pmu_device);
        if (access(check, R_OK) != 0) break;

        for (int e = 0; e < n; e++) {
            uint32_t type = 0;
            uint64_t config = 0;
            if (resolve_cha_event(pmu_device, events[e], &type, &config) < 0) {
                fprintf(stderr, "pmu_cha_open: cannot resolve event[%d]='%s' on %s\n",
                        e, events[e], pmu_device);
                /* Open with zero config so the fd slot is marked invalid */
                g->fds[tile][e] = -1;
                continue;
            }

            struct perf_event_attr attr = {0};
            attr.type        = type;
            attr.size        = sizeof(attr);
            attr.config      = config;
            attr.disabled    = 1;
            attr.read_format = PERF_FORMAT_TOTAL_TIME_ENABLED |
                               PERF_FORMAT_TOTAL_TIME_RUNNING;

            /* Uncore events: cpu=-1 for system-wide, or pin to a cpu on the socket */
            int pin_cpu = base + (tile % 32);  /* one cpu per tile */
            int fd = (int)perf_event_open(&attr, -1, pin_cpu, -1, 0);
            if (fd < 0) {
                fprintf(stderr, "pmu_cha_open: perf_event_open tile=%d ev=%s cpu=%d: %s\n",
                        t, events[e], pin_cpu, strerror(errno));
                g->fds[tile][e] = -1;
            } else {
                g->fds[tile][e] = fd;
            }
        }
        tile++;
    }
    g->n_tiles = tile;
    if (tile == 0) {
        fprintf(stderr, "pmu_cha_open: no CHA tiles found for socket %d\n", socket);
        return -1;
    }

    /* Enable all */
    for (int t = 0; t < g->n_tiles; t++)
        for (int e = 0; e < g->n_events; e++)
            if (g->fds[t][e] >= 0)
                ioctl(g->fds[t][e], PERF_EVENT_IOC_ENABLE, 0);

    return 0;
}

int pmu_cha_read(pmu_cha_group_t *g, uint64_t *totals)
{
    /* Format: { u64 value, u64 time_enabled, u64 time_running } */
    struct { uint64_t value, enabled, running; } buf;

    for (int e = 0; e < g->n_events; e++) totals[e] = 0;

    for (int t = 0; t < g->n_tiles; t++) {
        for (int e = 0; e < g->n_events; e++) {
            if (g->fds[t][e] < 0) continue;
            if (read(g->fds[t][e], &buf, sizeof(buf)) != sizeof(buf)) continue;
            /* Scale for PMU multiplexing */
            if (buf.running > 0)
                totals[e] += buf.value * buf.enabled / buf.running;
        }
    }
    return 0;
}

void pmu_cha_reset(pmu_cha_group_t *g)
{
    for (int t = 0; t < g->n_tiles; t++)
        for (int e = 0; e < g->n_events; e++)
            if (g->fds[t][e] >= 0)
                ioctl(g->fds[t][e], PERF_EVENT_IOC_RESET, 0);
}

void pmu_cha_close(pmu_cha_group_t *g)
{
    for (int t = 0; t < g->n_tiles; t++)
        for (int e = 0; e < g->n_events; e++)
            if (g->fds[t][e] >= 0) { close(g->fds[t][e]); g->fds[t][e] = -1; }
}

/* ── Core PMU ────────────────────────────────────────────────────────────── */

static int resolve_core_event(const char *name, struct perf_event_attr *attr)
{
    memset(attr, 0, sizeof(*attr));
    attr->size = sizeof(*attr);

    /* Handle well-known hardware events */
    if (strcmp(name, "cycles") == 0) {
        attr->type   = PERF_TYPE_HARDWARE;
        attr->config = PERF_COUNT_HW_CPU_CYCLES;
        return 0;
    }
    if (strcmp(name, "cache-misses") == 0 ||
        strcmp(name, "LLC-load-misses") == 0) {
        attr->type   = PERF_TYPE_HW_CACHE;
        attr->config = (PERF_COUNT_HW_CACHE_LL) |
                       (PERF_COUNT_HW_CACHE_OP_READ << 8) |
                       (PERF_COUNT_HW_CACHE_RESULT_MISS << 16);
        return 0;
    }

    /* Raw event: r<hex> */
    if (name[0] == 'r' || name[0] == 'R') {
        uint64_t raw;
        if (sscanf(name + 1, "%lx", &raw) == 1) {
            attr->type   = PERF_TYPE_RAW;
            attr->config = raw;
            return 0;
        }
    }

    fprintf(stderr, "resolve_core_event: unknown event '%s'\n", name);
    return -1;
}

int pmu_core_open(pmu_core_group_t *g, int cpu, const char **events, int n)
{
    g->n = 0;
    for (int i = 0; i < n; i++) {
        struct perf_event_attr attr;
        if (resolve_core_event(events[i], &attr) < 0) {
            g->fds[i] = -1;
            continue;
        }
        attr.disabled    = 0;
        attr.exclude_kernel = 1;
        attr.read_format = 0;
        int fd = (int)perf_event_open(&attr, 0, cpu, -1, 0);
        if (fd < 0) {
            fprintf(stderr, "pmu_core_open: %s on cpu%d: %s\n",
                    events[i], cpu, strerror(errno));
            g->fds[i] = -1;
        } else {
            g->fds[i] = fd;
            g->n++;
        }
    }
    return (g->n > 0) ? 0 : -1;
}

int pmu_core_read(pmu_core_group_t *g, uint64_t *values)
{
    for (int i = 0; i < g->n; i++) {
        values[i] = 0;
        if (g->fds[i] >= 0) {
            ssize_t n = read(g->fds[i], &values[i], sizeof(values[i]));
            (void)n;
        }
    }
    return 0;
}

void pmu_core_close(pmu_core_group_t *g)
{
    for (int i = 0; i < g->n; i++)
        if (g->fds[i] >= 0) { close(g->fds[i]); g->fds[i] = -1; }
}
