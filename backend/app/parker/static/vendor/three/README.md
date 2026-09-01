# Vendored Three.js (pinned)

- Package: `three@0.185.1` (npm), files `build/three.module.min.js` and
  `build/three.core.min.js` (the module build imports the core build)
- License: MIT (see `LICENSE` in this directory)
- Source: https://github.com/mrdoob/three.js
- Retrieved: 2026-08-31 via `npm pack three@0.185.1`
- SHA-256 `three.module.min.js`: `86bcee248b64f44bcfc23c331ae74619061957d59cab040171dcb6fb5900beb6`
- SHA-256 `three.core.min.js`: `05b2609338c76cd65daf74f3ac515bc9a5045e1b3b33edc07d8c9bd55250fa90`

Why vendored: the Converse page must never fetch runtime code from a CDN
(offline home deployment, packaged Tauri sidecar, no third-party calls
from the patient surface). Served same-origin by the engine at
`/parker/converse/static/vendor/three/three.module.min.js`.

To upgrade: `npm pack three@<version>`, copy `package/build/three.module.min.js`,
`package/build/three.core.min.js`, and `package/LICENSE` here, update the
version + SHA-256 pins above, and re-run
`backend/tests/test_converse_assets.py` (it pins the hashes).
