# SiroQ load benchmark — run it on any machine (Windows included)

This probes how fast the analysis service ingests + analyzes files and how much
memory it needs. No database, no Docker, no network — it only fires the exact
`ingestion` / `pipeline` code the HTTP endpoints use, so results apply to the
real service. The numbers behind `docs/CAPACITY.md` were produced by this
harness.

- `bench_runner.py` — measures one file (or a whole folder) per JSON line:
  size, rows, columns, `read_s` (parse), `analyze_s` (full pipeline) and peak
  memory.
- `gen_capacity.py` — generates a synthetic 16-column sales CSV with N rows so
  you get reproducible measurements without real data.

## 1. Install (Windows, PowerShell)

Tested on Windows 10/11 + Linux. You need Python **3.12 or newer** (64-bit,
from https://python.org — tick *"Add python.exe to PATH"*).

```powershell
# in the repo folder
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
# if the activation is blocked by policy, first run:
#   Set-ExecutionPolicy -Scope Process RemoteSigned

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install psutil    # optional, but gives exact memory on Windows
```

> `psutil` is only needed for precise peak-memory on Windows. Without it the
> harness falls back to Python-heap tracking (a lower-bound estimate — still
> useful, just conservative).

Linux equivalent: `python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt psutil`.

> **Tip:** close other heavy apps (browsers, editors) while measuring. Memory
> numbers are only trustworthy when the machine isn't swapping.

## 2. Generate test sizes (optional)

```powershell
python bench\gen_capacity.py 50000  bench\out50k.csv    # 5.5 MB, 50 k rows
python bench\gen_capacity.py 250000 bench\out250k.csv   # 27 MB, 250 k rows
python bench\gen_capacity.py 1000000 bench\out1m.csv    # 110 MB, 1 M rows
```

## 3. Measure

One file:

```powershell
python bench\bench_runner.py analyze "C:\Users\you\bench\out50k.csv"
```

A whole folder of your real files (`.xls`, `.xlsx`, `.csv` — every file):

```powershell
python bench\bench_runner.py analyze "C:\Users\you\Desktop\invoices"
```

Each line is JSON. Example:

```json
{"scenario":"out50k.csv","path":"bench\\out50k.csv","size":5515939,"rows":50000,"cols":16,"read_s":0.20,"analyze_s":11.6,"peak_kb":119984}
```

- `analyze_s` is what matters for capacity: the time one worker spends on one file.
- `peak_kb` is the memory for one concurrent analysis.
- `analyze` mode prints two lines per file: parse-only, then parse+analyze.

Linux: same commands, `bench/bench_runner.py`.

## 4. What to send back

To compare against the model in `docs/CAPACITY.md`:

1. The JSON lines (copy/paste of the output).
2. Your machine specs:

```powershell
Get-CimInstance Win32_Processor | Select-Object Name, NumberOfLogicalProcessors
Get-CimInstance Win32_ComputerSystem | Select-Object TotalPhysicalMemory
```

3. Whether the run was with `psutil` installed (memory precise) or not (floor).

## 5. How to read it (10-second version)

- Per-core budget `≈ 90 MB + rows × 0.6 KB` for a 16-column file → a 50 k-row
  file needs ~120 MB; a 250 k-row file ~240 MB.
- One core handles roughly `15 µs × columns × rows` per file. Two cores ≈ two
  simultaneous files. `analyze_s` on your machine × number of workers you plan
  to run = how long a batch of files takes.
- Full worked scenarios are in `docs/CAPACITY.md` (§3 cost model, §4 hard
  limits, §5 sizing table, §7 recommendations).