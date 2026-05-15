#pragma once
#include <stdint.h>
#include <linux/perf_event.h>

/* Opens a perf_event_open group for uncore CHA events summed across all
 * tiles on a given socket.
 *
 * Usage:
 *   struct pmu_group g;
 *   pmu_cha_open(&g, socket, events, n_events);
 *   pmu_group_enable(&g);
 *   ... workload ...
 *   pmu_group_read(&g, values);
 *   pmu_group_close(&g);
 */

#define PMU_MAX_EVENTS 8
#define PMU_MAX_TILES  32

typedef struct {
    int    leader_fd;
    int    fds[PMU_MAX_TILES][PMU_MAX_EVENTS];
    int    n_tiles;
    int    n_events;
    char   event_names[PMU_MAX_EVENTS][64];
} pmu_cha_group_t;

/* Open one perf group per CHA tile; events[i] are PMU event names like
 * "unc_cha_core_snp.evict_one". socket selects the PMU device numbering.
 * Returns 0 on success. */
int pmu_cha_open(pmu_cha_group_t *g, int socket, const char **events, int n);

/* Read summed counts across all tiles for each event. */
int pmu_cha_read(pmu_cha_group_t *g, uint64_t *totals);

void pmu_cha_close(pmu_cha_group_t *g);
void pmu_cha_reset(pmu_cha_group_t *g);

/* Simple core-PMU helpers (for victim LLC miss counting). */
typedef struct {
    int fds[PMU_MAX_EVENTS];
    int n;
} pmu_core_group_t;

int  pmu_core_open(pmu_core_group_t *g, int cpu, const char **events, int n);
int  pmu_core_read(pmu_core_group_t *g, uint64_t *values);
void pmu_core_close(pmu_core_group_t *g);
