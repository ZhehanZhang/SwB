# Visual regression

Compare the console with the saved images in the pinned Linux container.

Screenshots cover 375, 1024 and 1440 in both themes. The open Inbox conversation also has 1920 baselines for the wide two-pane layout. Load shift and overflow are checked at all five widths where the view is available. The server shell has a separate browser suite.

```sh
bash web/tests/visual/run.sh
```

Regenerate all images from the three synthetic fixtures.

```sh
bash web/tests/visual/run.sh --update-snapshots=all
SWB_VISUAL_REUSE_IMAGE=1 bash web/tests/visual/run.sh --compact-baselines
SWB_VISUAL_REUSE_IMAGE=1 bash web/tests/visual/run.sh
git diff --stat -- web/tests/visual/__screenshots__
```

Use the CI workflow on a `dev/` branch to regenerate, verify and commit the images.

The optimizer keeps CSS dimensions, uses lossless PNG encoding and may round each RGB channel to 32 levels. Rounding changes any channel by at most four 8-bit steps, well below Playwright's per-pixel threshold. A second comparison run checks the compacted baselines.

Apply the 0.05 layout shift limit to every case, including the cases listed for PR B.

```sh
SWB_VISUAL_STRICT_CLS=1 bash web/tests/visual/run.sh
```

Chromium emits breakpoint events during full-page capture. The agent sheet keeps its selected tab during the capture. Each test measures the route first.
