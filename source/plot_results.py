import json
from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np

folder = Path(__file__).resolve().parent
data_file = folder / "perf_results.jsonl"

records = {}

with open(data_file) as f:
    for line in f:
        if line.strip():
            item = json.loads(line)
            records[item["label"]] = item

expected = [
    "rr_10", "rr_25", "rr_50",
    "adaptive_10", "adaptive_25", "adaptive_50"
]

missing = [label for label in expected if label not in records]

if missing:
    raise SystemExit(f"Missing experiment results: {missing}")

clients = [10, 25, 50]
x = np.arange(len(clients))
width = 0.35

metrics = [
    ("avg_ms", "Average Latency (ms)", "average_latency.png"),
    ("p95_ms", "P95 Latency (ms)", "p95_latency.png"),
    ("throughput_rps", "Throughput (requests/sec)", "throughput.png")
]

for key, title, filename in metrics:
    rr = [records[f"rr_{n}"][key] for n in clients]
    adaptive = [records[f"adaptive_{n}"][key] for n in clients]

    plt.figure(figsize=(9, 5))
    plt.bar(x - width/2, rr, width, label="Round Robin")
    plt.bar(x + width/2, adaptive, width, label="Adaptive")
    plt.xticks(x, [f"{n} clients" for n in clients])
    plt.ylabel(title)
    plt.title(title + ": Round Robin vs Adaptive")
    plt.legend()
    plt.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(folder / filename, dpi=200)
    plt.close()

aliases = {
    "10.0.0.11": "srv1",
    "10.0.0.12": "srv2",
    "10.0.0.13": "srv3",
    "server1": "srv1",
    "server2": "srv2",
    "server3": "srv3"
}

labels = [
    "RR 10", "RR 25", "RR 50",
    "Adaptive 10", "Adaptive 25", "Adaptive 50"
]

plt.figure(figsize=(11, 6))

for server in ["srv1", "srv2", "srv3"]:
    values = []
    for label in expected:
        counts = {"srv1": 0, "srv2": 0, "srv3": 0}
        for name, count in records[label]["per_server"].items():
            name = aliases.get(name, name)
            if name in counts:
                counts[name] += count
        values.append(counts[server])

    plt.plot(labels, values, marker="o", label=server)

plt.title("Server Request Distribution")
plt.xlabel("Experiment")
plt.ylabel("Requests handled")
plt.legend()
plt.grid(alpha=0.3)
plt.tight_layout()
plt.savefig(folder / "server_distribution.png", dpi=200)
plt.close()

print("All four graphs generated successfully:")
for filename in [
    "average_latency.png",
    "p95_latency.png",
    "throughput.png",
    "server_distribution.png"
]:
    print(filename)
