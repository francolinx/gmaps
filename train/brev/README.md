# Brev fallback: train the parser LoRA off the Spark (and, on a laptop-only day, run the whole stack there)

Every team gets Brev credits for the weekend. Use them if the Spark cannot train in time (smoke fails on memory, the run is too slow,
or the GPU is needed for serving). Inference stays on the Spark: GMAPS's `LocalLLM` refuses non-local endpoints by design, so a Brev
box only produces weights that come back.

Nothing in this folder was run against a real GPU before Saturday; `brev_train.sh` is the same `train/local_lora.py` with the same
defaults, so a Brev adapter and a Spark adapter are interchangeable (the report.json records which machine trained it).

## 1. On the Spark: build the kit (3 files, 25 MB, synthetic only)

```bash
train/brev/brev_kit.sh                              # -> ~/gmaps_venue/brev_trainkit/ + .tar.gz; scans for secrets / private markers
HF_USER=<you> train/brev/brev_kit.sh --push         # or push it to a PRIVATE HF dataset repo (delete it after the event)
```

## 2. On the Brev console (https://brev.nvidia.com): one instance

- GPU: 1× L40S (48 GB) or A100 40/80 GB or H100. The 4B LoRA at batch 8 / max-len 1280 with gradient checkpointing needs well under 40 GB.
  A 1.7B run needs less. Do not pay for multi-GPU.
- Image: any CUDA 12 base with Python 3.10+ (the default "VM mode" Ubuntu image is fine; `brev_train.sh` builds its own venv and installs torch).
- Disk: 60 GB+ (base model 8 GB, torch, outputs).
- Then on your laptop (not the Spark): `bash -c "$(curl -fsSL https://raw.githubusercontent.com/brevdev/brev-cli/main/bin/install-latest.sh)"`
  (macOS: `brew install brevdev/homebrew-brev/brev`), `brev login`, `brev refresh`, `brev shell <instance>`.

## 3. On the instance

```bash
# kit from the private HF repo:
HF_TOKEN=<token> HF_USER=<you> bash brev_train.sh <you>/gmaps-trainkit-private
# or kit copied by scp (after `brev refresh`, the instance name is an ssh alias):
#   scp ~/gmaps_venue/brev_trainkit.tar.gz <instance>:~/ ; ssh <instance> 'tar -xzf brev_trainkit.tar.gz'
#   bash ~/brev_trainkit/brev_train.sh ~/brev_trainkit
# knobs: EPOCHS=1 BATCH=8 MAXLEN=1280 BASE=Qwen/Qwen3-1.7B   (1.7B = the Lightning configuration's parser)
```

Expected: a few minutes of setup, then the run (an L40S does ~7k pairs × 2 epochs in roughly 30–60 min; the script prints
`train_runtime_s`). Output: `~/gmaps_out/<run>/adapter`, `merged_fp16`, `report.json`, a tarball, and with `HF_TOKEN`+`HF_USER`
a private model repo `<you>/<run>-private`.

## 4. Back on the Spark

```bash
~/ftvenv/bin/hf download <you>/<run>-private --local-dir ~/gmaps_venue/adapters/<run>          # or untar the scp'd tarball there
docker run --rm --gpus all -p 127.0.0.1:8001:8000 -v /mnt/hackssd/GB10_ARSENAL/models/reasoning/qwen3-4b-instruct:/base:ro \
  -v ~/gmaps_venue/adapters/<run>/adapter:/adapter:ro vllm/vllm-openai:v0.27.1 --model /base --served-model-name base \
  --enable-lora --lora-modules gmaps-parser=/adapter --max-lora-rank 16 --gpu-memory-utilization 0.15 --enable-auto-tool-choice --tool-call-parser hermes
```
Then the Saturday sequence continues unchanged (offline runner, `gym eval run`, tool bench, scoreboard, `tools/publish_release.py --run ~/gmaps_venue/adapters/<run>`).

## Laptop-only day (the Spark stays in the hotel): the whole stack on one Brev box

`brev_stack.sh` is syntax-checked only; it was never run on a real Brev instance.

1. **Boot one instance** on the Brev console: 1× L40S (48 GB) or A100. Pick an image with docker and the NVIDIA container runtime, and 80 GB+ disk.
   On the laptop: `brev login`, `brev refresh`.
2. **Get the code and adapter onto it.**
   - Code from the SSD: `scp /Volumes/<ssd>/GB10_ARSENAL/raleigh_2026-10-17/repo.bundle <instance>:~/`.
   - Adapter: Franco's private HF mirror (`darkstar008/<run>-private`), or
     `tar -czf adapter.tar.gz -C <restored gmaps_venue>/adapters <run>/adapter` and scp it.
3. **`brev shell <instance>`**, then run one of:
   - `BUNDLE=~/repo.bundle RUN=<run> HF_USER=darkstar008 HF_TOKEN=<token> bash ~/gmaps/train/brev/brev_stack.sh`
     (clone the bundle first, or copy the script on its own).
   - `BUNDLE=~/repo.bundle ADAPTER_TAR=~/adapter.tar.gz bash brev_stack.sh`

   What it does:
   - pulls Qwen3-4B-Instruct-2507;
   - serves `base` + `gmaps-parser` on 127.0.0.1:8001 with the same vLLM v0.27.1 / hermes / `--enable-lora` command as the Spark;
   - installs nemo-gym in a venv;
   - runs the one-shot check and prints the demo commands.
4. **Rehearse** with `demo/run_track.sh <track>` (or `docs/DEMO_RUNBOOK_RALEIGH.md`) inside `brev shell`.
   - Compare each track with its capture in `docs/demo_captures/`.
5. **Not available on Brev:**
   - the Kokoro audio simulator and the real-time Whisper path. Show the `docs/demo_captures/` recordings, plus the TEXT (`--text`) and REPLAY (`--replay`) simulator modes instead;
   - the NemoClaw/OpenShell sandbox. It is configured on the Spark only.
6. **Afterwards:** stop the server, **delete the instance**, and delete the private adapter repo after the event.

**Inference locality.** Run everything inside `brev shell`: the runtime and vLLM on one box, so `LocalLLM` sees 127.0.0.1.
`brev port-forward <instance> --port 8001:8001` also works, but the laptop's "local" endpoint is then a cloud GPU.
That stretches CLAUDE.md's "inference stays local" rule. It is Franco's call, and the pitch must say where inference ran.

## Rules that still hold on Brev

Synthetic data only leaves the Spark (the kit scan refuses anything else). No real audio, no INTERNAL files, no tokens in files
(`HF_TOKEN` is an environment variable, never written). Delete the instance and the private kit repo after the event. Say in the
pitch where the adapter was trained; the parameter cap is about the judged system, not where training ran.

Sources: Brev CLI getting started and connectivity pages (docs.nvidia.com/brev, read Oct 17 2026).
