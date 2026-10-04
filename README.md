# Wall Mould Inspection with White Light and UV

Capture a white-light image and an optional 365 nm UV image of a wall. The service returns an L1–L5 severity grade, affected areas, and recommended actions.

**Measurements come from OpenCV; the language model interprets the evidence.** OpenCV produces the reported numbers, so results remain reproducible and an algorithm-only result is available when the model cannot be reached. The model grades the evidence, reviews candidate regions, and suggests possible causes. Disagreements between the two grades appear explicitly in the report.

## Quick start

Requires Python 3.10 or later.

```bash
./setup.sh          # Create .venv, install dependencies, and create .env
vi .env             # Set LLM_BASE_URL, LLM_API_KEY, and LLM_MODEL
./run.sh            # Start the service and display local network URLs
```

If several Python versions are installed, run `PYTHON=python3.12 ./setup.sh`. To set up manually:

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open `http://<server-ip>:8000` in a browser.

### Mobile capture and HTTPS

Browsers allow `getUserMedia` only in a secure context, such as HTTPS or localhost. Over HTTP on a local network, a phone can still use its system camera or select a saved image, but live preview and the alignment overlay are unavailable.

For live preview and the alignment overlay:

```bash
./run.sh --https                     # Generate a self-signed certificate if needed
scripts/make_cert.sh 192.168.1.23    # Regenerate it when the local IP changes
```

The certificate includes the local IP in its Subject Alternative Name. A phone may show a warning for this self-signed certificate on first access.

For Docker:

```bash
docker compose up -d --build
```

If the model server runs on the Docker host, set `LLM_BASE_URL=http://host.docker.internal:8100/v1` in `.env`. `docker-compose.yml` includes an optional vLLM service example.

## Configuration

Copy `.env.example` to `.env` and adjust these settings:

| Variable | Purpose |
| --- | --- |
| `LLM_BASE_URL` | OpenAI-compatible endpoint, for example `http://127.0.0.1:8100/v1` |
| `LLM_API_KEY` | API key; a placeholder may work for a local server |
| `LLM_MODEL` | Model name exposed by the endpoint; the example is `qwen3.6-35b-a3b` |
| `LLM_JSON_MODE` | Automatically falls back if the endpoint does not support `response_format` |
| `REFERENCE_MODE` | `anchor` (default: L1/L3/L5 UV references), `none`, or `full` |
| `GRADING_STRATEGY` | `llm_led` (default) or `cv_led` |
| `ENABLE_CALIBRATION_CARD` | Enable calibration-card detection |
| `RETENTION_DAYS` | Number of days to retain images and records; default: 30 |
| `STORAGE_DIR` | Local disk directory; SQLite may fail to lock files on NFS or SMB mounts |

The grading criteria and fallback thresholds live in `config/grading.yaml`. Changes are reloaded without restarting the service. The `*_zh` and `*_en` fields in that file support both product languages.

## API

```text
POST   /api/v1/analyze                  Upload white and optional UV images; returns 202 and task_id
GET    /api/v1/tasks/{id}/stream        Server-sent progress events
GET    /api/v1/tasks/{id}               Final JSON result
GET    /api/v1/tasks/{id}/assets/{f}    Image assets
DELETE /api/v1/tasks/{id}               Delete a task and its assets
GET    /api/v1/config                   Frontend configuration
GET    /api/v1/health                   Service and model connectivity
```

A result can be opened at `http://<host>/?task=<task_id>`. There are no user accounts; anyone with the link can access the result while it is retained.

## Processing pipeline

```text
Image checks → Registration → Segmentation and measurement → Model review → Arbitration → Report
```

1. **Image checks:** Reject images with insufficient resolution, excessive blur or exposure, or UV ambient-light contamination.
2. **Registration:** Align images with ORB and RANSAC, then try ECC if needed. If both fail, mark the images as unregistered and reduce confidence in comparison metrics.
3. **Segmentation and measurement:** Apply Lab-L flat-field correction and a clean-wall percentile threshold. Produce masks, contours, coverage, patch area, and blob counts. A calibration card enables brightness normalization and absolute area estimates.
4. **Model review:** Send the white-light image, UV image, and numbered mask overlay to the model. The model reviews numbered candidate regions rather than generating pixel coordinates.
5. **Arbitration:** Reward agreement between CV and model grades; for a one-level difference, use the higher grade; for a difference of two or more levels, flag the result for human review. Confidence is capped at 0.6 without UV and 0.75 without registration.
6. **Report:** Store source images, masks, the raw model response, and algorithm and threshold versions.

## Limits

- **Regions:** OpenCV supplies pixel masks and contours; the model does not supply coordinates.
- **Species:** Image appearance alone does not identify a fungal species. The report gives a morphology hint and recommends sampling where appropriate. `morphology_hint.confidence` is limited to `low` or `medium`.
- **Counts:** Blob counts are meaningful only for L1–L2. At L3 and above, colonies merge and the interface hides the count.

## Calibration card

```bash
python3 scripts/make_calibration_card.py card.png
```

This generates an 85.6 × 54 mm, 300 dpi card with four ArUco corner markers and white, grey, fluorescent, and black patches. Include the card in both images to estimate area in cm², normalize UV brightness, and correct white balance.

Print on matte paper with automatic color enhancement disabled. The fluorescent patch requires fluorescent ink or label stock; ordinary ink will not fluoresce at 365 nm. Measure the printed width and adjust `CALIB_CARD_WIDTH_MM` if it differs from 85.6 mm.

Without a card, the system still works, but UV coverage is marked `estimated` and absolute area is unavailable.

## UV light source

Use a **365 nm lamp with a ZWB2 or UG-1 filter**. A 395 nm lamp can leak enough visible purple light to obscure weak fluorescence.

## Checks

```bash
./.venv/bin/python scripts/ladder.py       # Reference-image coverage and grade checks
./.venv/bin/python scripts/smoke_test.py   # End-to-end API and fallback checks
./.venv/bin/python scripts/mock_llm.py     # Optional standalone mock model server
```

`smoke_test.py` starts its own mock model, so the third command is unnecessary for that test. Run `ladder.py` after changing segmentation parameters to check that white-light coverage increases monotonically across the reference grades.

| Metric | L1 | L2 | L3 | L4 | L5 |
| --- | ---: | ---: | ---: | ---: | ---: |
| White-light coverage | 2.2% | 3.8% | 14.7% | 51.0% | 73.7% |
| UV coverage | 9.0% | 19.6% | 53.6% | 55.0% | 78.2% |
| Hidden-spread ratio | 4.1× | 5.2× | 3.6× | 1.1× | 1.1× |

## Repository layout

```text
setup.sh                    Create the virtual environment and local configuration
run.sh                      Start the service; --https enables TLS
scripts/make_cert.sh        Generate a self-signed certificate
app/                        API, pipeline, CV, model client, and storage
web/                        Framework-free single-page interface
config/grading.yaml         Reloadable grading criteria and thresholds
reference/                  L1–L5 reference images and regression inputs
```

## Disclaimer

This tool is for screening. It does not provide an indoor environmental assessment, health or medical advice, or species identification. Qualified professionals should handle severe L4/L5 contamination.
