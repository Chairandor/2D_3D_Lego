# UniTEX external runners

These scripts live outside the upstream repository. They do not edit UniTEX.

`run_stage1_external.py` runs only reference preprocessing, mesh preprocessing,
geometry rendering, six-view texturing, and delighting. It intentionally skips
LTM construction and final texture projection so `mv_rgb.png` can be reviewed
before the expensive final stage.

`run_unitex_external.py` is the pipeline runner. Its `stage1` and `project`
phases can be executed separately, so an accepted six-view sheet is cached and
the final `textured_mesh.glb` can be resumed without regenerating it. `ltm`
projection fills surfaces that are not visible in the generated views; `direct`
is a faster diagnostic option.

For an official RGB example, use `--reference-mode official`. For an already
approved transparent cutout, use `--reference-mode rgba` to preserve its alpha
mask and bypass UniTEX's RMBG-2.0 pass.
