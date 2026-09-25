# ai-neko MVP1 third-party assets

These files retain their own licenses. The project license does not replace them.

| Component | Scope | License |
| --- | --- | --- |
| YUI Lolita, Project N.E.K.O. bundled character | `../assets/yui-lolita/` character, texture, motions and expressions | Upstream Apache-2.0 LICENSE and contribution declaration; see scope below |
| PixiJS 7.4.3 | `pixi.min.js` | MIT |
| Pixi CSP adapter 7.4.3 | `pixi-unsafe-eval.min.js` | MIT; avoids `new Function` in a strict CSP |
| pixi-live2d-display-lipsyncpatch 0.5.0-ls-6 | `cubism4.min.js` | MIT for the wrapper, Live2D Open Software License for embedded Cubism framework |
| Live2D Cubism Core | `live2dcubismcore.min.js`, provisioned at build time | Live2D Proprietary Software License; unmodified Redistributable Code |

Full license text, original license-page snapshots, and original notices are in
`licenses/`. `sources.json` records versions, source URLs, fixed commits, the Core
hash and the end-user terms hash. The project asset manifest records every file
hash. The Core is included only in the functional app package, not committed as
a standalone source-repository copy. `fetch-core.cjs` restores the exact copy
from the user-designated N.E.K.O. reference and verifies all asset/license hashes.

Character attribution:

Project N.E.K.O.
Copyright 2025-2026 Project N.E.K.O. Team
Copyright (c) 2025 Hongzhi Wen

YUI Lolita is extracted byte-for-byte from the user-designated N.E.K.O.
`assets/yui-lolita.tar.gz`, retaining its required model resources. It is not a
Live2D official sample character. Reuse relies on the repository's Apache-2.0
LICENSE, NOTICE and CONTRIBUTING license declaration, the maintainer's submitted
and merged first-party bundled model, and the absence of an identified
YUI-specific exception. This is an engineering interpretation of those upstream
declarations, not an independent chain-of-title certification. The corresponding
source commit, archive digest and per-file hashes are recorded in the manifest.
Original N.E.K.O. LICENSE and NOTICE and its contribution-license declaration are
retained in `licenses/`. ai-neko does not claim authorship of the character or
endorsement by Project N.E.K.O.

The separately licensed Live2D SDK is not covered by either project's Apache
license. The initial app presents its full applicable terms before loading the
character and Core. Later business use and model extensibility must be assessed
under the original SDK terms; this notice does not grant additional rights.
