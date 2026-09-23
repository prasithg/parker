# Reachy Mini shell geometry

Source: [Pollen Robotics / Reachy Mini](https://github.com/pollen-robotics/reachy_mini/tree/234a978e4426895fc88d864e7f154643aea77f53/src/reachy_mini/descriptions/reachy_mini/mjcf/assets).
Upstream revision: `234a978e4426895fc88d864e7f154643aea77f53`.
Copyright Pollen Robotics; distributed under Apache License 2.0 (see LICENSE).
These are the official CAD shell shapes, not a scan of a particular robot.

Parker modifications: six selected binary STL shells were vertex-welded at
10 micrometres and packed into indexed, quantized buffers. No triangles were
decimated. `reachy-mini.json` records the upstream revision, SHA-256 of every
source STL, buffer ranges, and SHA-256 of the packed binary. Materials,
optical lenses, antenna springs, articulated neck rods, and state-driven
motion are Parker presentation code in `converse/reachy-model.js` and
`converse/reachy.js`. The neck is a visual linkage, not a hardware controller
or a physical kinematics simulation.

To reproduce, download the six filenames listed in `scripts/build_reachy_mesh.py`
from the pinned upstream `mjcf/assets` directory into a temporary directory,
then run:

```sh
backend/.venv/bin/python scripts/build_reachy_mesh.py /path/to/downloaded/stls backend/app/parker/static/vendor/reachy-mini
```

The official source uses Git LFS; fetch binary contents using the
`media.githubusercontent.com/media/pollen-robotics/reachy_mini/<revision>/...`
URL. The converter uses NumPy (already a backend voice dependency) only at
build time. Browser loads are same-origin and total approximately 1.65 MB.
No provider call or external asset request occurs during rendering.
