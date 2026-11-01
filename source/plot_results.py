#!/usr/bin/env python3
"""Generate the four project graphs from ../results/perf_results.jsonl.
Run from the repository root with: python source/plot_results.py
"""
import json
from pathlib import Path
import matplotlib.pyplot as plt

SOURCE_DIR = Path(__file__).resolve().parent
ROOT_DIR = SOURCE_DIR.parent
RESULTS_FILE = ROOT_DIR / "results" / "perf_results.jsonl"
GRAPH_DIR = ROOT_DIR / "graphs"
GRAPH_DIR.mkdir(parents=True, exist_ok=True)

EXPECTED = ["rr_10", "rr_25", "rr_50", "adaptive_10", "adaptive_25", "adaptive_50"]
CLIENTS = [10, 25, 50]


def load_results():
    if not RESULTS_FILE.exists():
        raise SystemExit(f"Results file not found: {RESULTS_FILE}")
    records = {}
    with RESULTS_FILE.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"Invalid JSON on line {line_number}: {exc}")
            label = record.get("label")
            if label:
                records[label] = record
    missing = [label for label in EXPECTED if label not in records]
    if missing:
        raise SystemExit("Missing experiment labels: " + ", ".join(missing))
    return records


def grouped_bar(records, metric, ylabel, filename):
    positions = range(len(CLIENTS))
    width = 0.36
    rr = [records[f"rr_{n}"].get(metric) for n in CLIENTS]
    adaptive = [records[f"adaptive_{n}"].get(metric) for n in CLIENTS]
    if any(value is None for value in rr + adaptive):
        raise SystemExit(f"Missing metric {metric!r} in one or more experiment records")

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar([p - width / 2 for p in positions], rr, width, label="Round Robin")
    ax.bar([p + width / 2 for p in positions], adaptive, width, label="Adaptive")
    ax.set_xticks(list(positions), [f"{n} clients" for n in CLIENTS])
    ax.set_ylabel(ylabel)
    ax.set_title(f"{ylabel}: Round Robin vs Adaptive")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(GRAPH_DIR / filename, dpi=200)
    plt.close(fig)


def server_distribution(records):
    aliases = {
        "10.0.0.11": "srv1", "server1": "srv1", "srv1": "srv1",
        "10.0.0.12": "srv2", "server2": "srv2", "srv2": "srv2",
        "10.0.0.13": "srv3", "server3": "srv3", "srv3": "srv3",
    }
    labels = ["RR 10", "RR 25", "RR 50", "Adaptive 10", "Adaptive 25", "Adaptive 50"]
    fig, ax = plt.subplots(figsize=(11, 5.5))
    for server in ("srv1", "srv2", "srv3"):
        values = []
        for label in EXPECTED:
            counts = {"srv1": 0, "srv2": 0, "srv3": 0}
            for name, count in records[label].get("per_server", {}).items():
                canonical = aliases.get(name)
                if canonical:
                    counts[canonical] += int(count)
            values.append(counts[server])
        ax.plot(labels, values, marker="o", label=server)
    ax.set_title("Server Request Distribution")
    ax.set_xlabel("Experiment")
    ax.set_ylabel("Requests handled")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(GRAPH_DIR / "server_distribution.png", dpi=200)
    plt.close(fig)


def main():
    records = load_results()
    grouped_bar(records, "avg_ms", "Average Latency (ms)", "average_latency.png")
    grouped_bar(records, "p95_ms", "P95 Latency (ms)", "p95_latency.png")
    grouped_bar(records, "throughput_rps", "Throughput (requests/sec)", "throughput.png")
    server_distribution(records)
    print("Generated graphs in", GRAPH_DIR)
    for name in ("average_latency.png", "p95_latency.png", "throughput.png", "server_distribution.png"):
        print(" -", name)


if __name__ == "__main__":
    main()
