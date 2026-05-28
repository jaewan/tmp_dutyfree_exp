"""
runner.py — Shared orchestration: process management, pinning, PMU capture.

All experiment scripts import this module for consistent launch/teardown.
"""

import subprocess
import threading
import time
import json
import os
import sys
import signal
import datetime
import re
from pathlib import Path
from typing import List, Optional, Tuple, Dict

PROJECT_ROOT = Path(__file__).parent.parent
BENCH_DIR    = PROJECT_ROOT / "bench"
RESULTS_RAW  = PROJECT_ROOT / "results" / "raw"
RESULTS_PROC = PROJECT_ROOT / "results" / "processed"

VICTIM_BIN  = BENCH_DIR / "victim" / "pointer_chase"
AGGR_BINS   = {
    "A": BENCH_DIR / "aggressor" / "stream_wb",
    "B": BENCH_DIR / "aggressor" / "stream_wb_nopf",
    "C": BENCH_DIR / "aggressor" / "stream_wc",
    "D": BENCH_DIR / "aggressor" / "stream_nt",
    "E": BENCH_DIR / "aggressor" / "stream_wc_nopf",
}

# --- Topology config (CXL) -------------------------------------------------
# The aggressor streams from the CPU-less CXL NUMA node (CXL_NODE) while
# victim+aggressors run on the socket that node is attached to (CPU_NODE) with
# the victim's DRAM socket-local, so the streaming fills contend in that
# socket's shared LLC. Override per machine via CPU_NODE / CXL_NODE.
#   CPU_NODE  victim+aggressor cores and victim DRAM (socket-local).
#   CXL_NODE  aggressor stream memory (the CPU-less CXL node).
NUMA_NODE     = int(os.environ.get("CPU_NODE", "1"))   # victim cores + victim DRAM
AGGR_MEM_NODE = int(os.environ.get("CXL_NODE", "2"))   # aggressor stream (CXL node)

# Physical cores of the CPU_NODE socket. On this 2×32-core SPR box socket s owns
# the 32 logical CPUs [32*s, 32*s+32); HT siblings (64+) are excluded.
_SOCKET_CORES = list(range(32 * NUMA_NODE, 32 * NUMA_NODE + 32))
VICTIM_CPU  = _SOCKET_CORES[0]
AGGR_CPUS   = _SOCKET_CORES[1:]          # remaining physical cores


def timestamp() -> str:
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def log(msg: str):
    ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{ts}] {msg}", flush=True)


def check_binaries():
    for name, path in [("victim", VICTIM_BIN)] + list(AGGR_BINS.items()):
        if not path.exists():
            sys.exit(f"ERROR: binary not found: {path}\n  Run: make -C bench/")


def check_env():
    """Verify critical environment settings before any measurement."""
    errors = []

    paranoid = int(Path("/proc/sys/kernel/perf_event_paranoid").read_text().strip())
    if paranoid > 0:
        errors.append(f"perf_event_paranoid={paranoid} (need ≤ 0; run sudo env/setup.sh)")

    nb = int(Path("/proc/sys/kernel/numa_balancing").read_text().strip())
    if nb != 0:
        errors.append(f"numa_balancing={nb} (need 0; run sudo env/setup.sh)")

    gov = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor").read_text().strip()
    if gov != "performance":
        errors.append(f"governor={gov} (need performance; run sudo env/setup.sh)")

    try:
        no_turbo = int(Path("/sys/devices/system/cpu/intel_pstate/no_turbo").read_text().strip())
        if no_turbo != 1:
            errors.append("turbo is ENABLED (run sudo env/setup.sh)")
    except FileNotFoundError:
        errors.append("intel_pstate/no_turbo not found")

    if errors:
        print("ENVIRONMENT ERRORS (run sudo env/setup.sh to fix):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    log("env check: PASS")


def pin_cmd(cpu: int, node: int = NUMA_NODE) -> List[str]:
    return ["numactl", f"--physcpubind={cpu}", f"--membind={node}"]


def read_msr(cpu: int, reg: int = 0x1A4) -> Optional[int]:
    try:
        result = subprocess.run(
            ["rdmsr", "-p", str(cpu), hex(reg)],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return int(result.stdout.strip(), 16)
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        pass
    return None


def read_tsc_hz() -> int:
    """Read TSC frequency from kernel dmesg or cpuinfo."""
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if "cpu MHz" in line:
                    mhz = float(line.split(":")[1].strip())
                    return int(mhz * 1e6)
    except Exception:
        pass
    return 3_000_000_000  # fallback 3 GHz


class AggressorProcess:
    """Manages one aggressor process lifecycle."""

    def __init__(self, condition: str, cpu: int, region_gb: int = 1,
                 duration_sec: float = 90.0, node: int = NUMA_NODE,
                 page_kb: Optional[int] = None, readonly: bool = False):
        self.condition  = condition
        self.cpu        = cpu
        self.region_gb  = region_gb
        self.duration_sec = duration_sec
        self.node       = node
        # page_kb / readonly are only honored by stream_wb (condition A).
        self.page_kb    = page_kb
        self.readonly   = readonly
        self.proc: Optional[subprocess.Popen] = None
        self._stdout_data = ""
        self._stderr_lines: List[str] = []

    def start(self):
        bin_path = AGGR_BINS[self.condition]
        cmd = (pin_cmd(self.cpu, self.node) +
               [str(bin_path),
                "--cpu", str(self.cpu),
                "--node", str(self.node),
                "--region-gb", str(self.region_gb),
                "--duration-sec", str(int(self.duration_sec))])
        if self.page_kb is not None:
            cmd += ["--page-kb", str(self.page_kb)]
        if self.readonly:
            cmd += ["--readonly"]
        log(f"  aggressor [{self.condition}] cpu={self.cpu}: {' '.join(cmd)}")
        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        # Background stderr reader
        def _drain_stderr():
            for line in self.proc.stderr:
                self._stderr_lines.append(line.rstrip())
        threading.Thread(target=_drain_stderr, daemon=True).start()

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()

    def read_final_bw(self) -> Optional[float]:
        """Parse final JSON output from aggressor stdout."""
        try:
            out, _ = self.proc.communicate(timeout=10)
            data = json.loads(out.strip())
            return float(data.get("avg_bw_gbps", 0))
        except Exception:
            return None

    def get_recent_bw_from_stderr(self) -> Optional[float]:
        """Parse last reported BW from stderr progress lines."""
        pattern = re.compile(r"bw=([\d.]+) GB/s")
        for line in reversed(self._stderr_lines):
            m = pattern.search(line)
            if m:
                return float(m.group(1))
        return None


class VictimRun:
    """Runs victim pointer_chase and collects per-trial JSON."""

    def __init__(self, cpu: int = VICTIM_CPU, node: int = NUMA_NODE,
                 wss: int = 32 * 1024 * 1024, trials: int = 30,
                 run_sec: float = 1.0, pf_disable: bool = False,
                 page_kb: Optional[int] = None, warmup_trials: int = 0):
        self.cpu       = cpu
        self.node      = node
        self.wss       = wss
        self.trials    = trials
        self.run_sec   = run_sec
        self.pf_disable = pf_disable
        self.page_kb   = page_kb
        self.warmup_trials = warmup_trials
        self.results: List[Dict] = []

    def run(self) -> List[Dict]:
        cmd = (pin_cmd(self.cpu, self.node) +
               [str(VICTIM_BIN),
                "--cpu",    str(self.cpu),
                "--node",   str(self.node),
                "--wss",    str(self.wss),
                "--trials", str(self.trials),
                "--run-sec", f"{self.run_sec:.2f}"])
        if self.pf_disable:
            cmd.append("--pf-disable")
        if self.page_kb is not None:
            cmd += ["--page-kb", str(self.page_kb)]
        if self.warmup_trials > 0:
            cmd += ["--warmup-trials", str(self.warmup_trials)]

        log(f"  victim: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            log(f"  victim FAILED: {result.stderr[-500:]}")
            return []

        try:
            self.results = json.loads(result.stdout.strip())
            return self.results
        except json.JSONDecodeError as e:
            log(f"  victim JSON parse error: {e}\n  stdout: {result.stdout[:200]}")
            return []


def run_perf_cha_stat(duration_sec: float, cpu_range: str = "0-31") -> Dict:
    """
    Collect CHA uncore PMU counters for `duration_sec` seconds.
    Returns dict of {event_name: count}.

    Uses `perf stat` with system-wide uncore collection.
    Requires perf_event_paranoid ≤ 0.
    """
    events = [
        "uncore_cha_0/unc_cha_core_snp.evict_one/",
        "uncore_cha_0/unc_cha_core_snp.evict_gtone/",
        "uncore_cha_0/unc_cha_rxc_req_q1_retry.sf_victim/",
        "uncore_cha_0/unc_cha_tor_inserts.ia_drd_pref/",
        "uncore_cha_0/unc_cha_tor_inserts.ia_drd/",
    ]
    # Sum across all 32 CHA tiles by repeating for each tile
    all_tile_events = []
    for tile in range(32):
        for ev in events:
            all_tile_events.append(ev.replace("uncore_cha_0/", f"uncore_cha_{tile}/"))

    # perf stat -a -e <events> -- sleep N
    cmd = (["perf", "stat", "-a", "--no-big-num",
            "-e", ",".join(all_tile_events),
            "--", "sleep", f"{duration_sec:.1f}"])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=duration_sec + 30)
    except subprocess.TimeoutExpired:
        log("  perf stat timed out")
        return {}

    totals: Dict[str, int] = {}
    for line in result.stderr.splitlines():
        # Format: "     12,345  uncore_cha_N/event_name/"
        m = re.match(r'\s*([\d,]+)\s+uncore_cha_\d+/([\w.]+)/', line)
        if m:
            count = int(m.group(1).replace(",", ""))
            ev_name = m.group(2)
            totals[ev_name] = totals.get(ev_name, 0) + count

    if not totals:
        log(f"  WARNING: perf stat returned no counts. stderr: {result.stderr[:300]}")

    return totals


def save_raw(data: dict, tag: str = "trial") -> Path:
    RESULTS_RAW.mkdir(parents=True, exist_ok=True)
    path = RESULTS_RAW / f"{tag}_{timestamp()}.json"
    path.write_text(json.dumps(data, indent=2))
    return path


def warmup_sleep(seconds: float = 5.0):
    log(f"  warmup: sleeping {seconds:.0f} s for aggressors to stabilize")
    time.sleep(seconds)


def cooldown_sleep(seconds: float = 2.0):
    time.sleep(seconds)


# ─────────────────────────────────────────────────────────────────────────────
# resctrl (CAT / MBA) helpers — used by Phase 2/3. Require root + mounted resctrl.
# Algorithm ported from scripts/exp3_cat.sh and scripts/exp4_mba.sh.
# ─────────────────────────────────────────────────────────────────────────────
RESCTRL_ROOT = Path("/sys/fs/resctrl")


def resctrl_mounted() -> bool:
    return (RESCTRL_ROOT / "schemata").exists()


def require_resctrl():
    """Fail fast with a helpful message if resctrl is unusable (Phase 2/3)."""
    if os.geteuid() != 0:
        sys.exit("ERROR: this phase needs root for resctrl. Run with sudo.")
    if not resctrl_mounted():
        sys.exit("ERROR: resctrl not mounted. Run: sudo env/setup.sh "
                 "(or: sudo mount -t resctrl resctrl /sys/fs/resctrl)")


def resctrl_l3_total_ways() -> int:
    mask = int((RESCTRL_ROOT / "info" / "L3" / "cbm_mask").read_text().strip(), 16)
    return bin(mask).count("1")


def resctrl_l3_min_cbm_bits() -> int:
    try:
        return int((RESCTRL_ROOT / "info" / "L3" / "min_cbm_bits").read_text().strip())
    except Exception:
        return 1


def cpu_l3_cache_id(cpu: int) -> int:
    """L3 (resctrl) cache/domain id for a given logical CPU. The victim/aggressor
    cores run on the CXL host socket (CPU_NODE), whose L3 domain id is that socket
    number — so CAT/MBA must target this id, not a hardcoded 0."""
    for idx in (3, 2, 1, 0):  # index3 is L3 on SPR; fall back if absent
        p = Path(f"/sys/devices/system/cpu/cpu{cpu}/cache/index{idx}")
        if (p / "level").exists() and (p / "level").read_text().strip() == "3":
            return int((p / "id").read_text().strip())
    return 0


def resctrl_l3_cache_ids() -> List[int]:
    """Parse cache (socket) ids from the default group's L3 schemata line."""
    for line in (RESCTRL_ROOT / "schemata").read_text().splitlines():
        line = line.strip()
        if line.startswith("L3:"):
            body = line[len("L3:"):]
            return [int(seg.split("=")[0]) for seg in body.split(";") if "=" in seg]
    return [0]


def resctrl_mb_info() -> Tuple[int, int]:
    """Return (min_bandwidth_pct, bandwidth_gran_pct) for MBA."""
    mb = RESCTRL_ROOT / "info" / "MB"
    min_bw = int((mb / "min_bandwidth").read_text().strip())
    gran   = int((mb / "bandwidth_gran").read_text().strip())
    return min_bw, gran


def cat_way_masks(total_ways: int, victim_ways: int, aggr_ways: int) -> Tuple[str, str]:
    """Build contiguous, non-overlapping CBM masks: victim occupies the high
    `victim_ways`, aggressor the low `aggr_ways`. Returns (victim_hex, aggr_hex)
    without the 0x prefix (resctrl schemata format)."""
    victim_ways = max(1, min(victim_ways, total_ways))
    aggr_ways   = max(1, min(aggr_ways, total_ways - victim_ways if total_ways > victim_ways else 1))
    victim_mask = ((1 << victim_ways) - 1) << (total_ways - victim_ways)
    aggr_mask   = (1 << aggr_ways) - 1
    return f"{victim_mask:x}", f"{aggr_mask:x}"


def resctrl_make_group(name: str) -> Path:
    g = RESCTRL_ROOT / name
    g.mkdir(exist_ok=True)
    return g


def resctrl_write_schemata(group: Path, line: str):
    (group / "schemata").write_text(line + "\n")


def resctrl_set_cpus(group: Path, cpus: List[int]):
    (group / "cpus_list").write_text(",".join(str(c) for c in cpus) + "\n")


def resctrl_teardown(names: List[str]):
    """Remove resctrl groups; ignore errors so it is safe in a finally block."""
    for name in names:
        try:
            (RESCTRL_ROOT / name).rmdir()
        except (FileNotFoundError, OSError) as e:
            log(f"  resctrl teardown: could not rmdir {name}: {e}")
