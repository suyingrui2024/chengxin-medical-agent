# Third-party components

The application bundles the original, unmodified Noto Sans CJK SC Regular and Noto Serif CJK SC Regular font files from https://github.com/notofonts/noto-cjk at commit `f8d157532fbfaeda587e826d4cd5b21a49186f7c`.

- Noto Sans CJK SC: © 2014–2021 Adobe (http://www.adobe.com/).
- Noto Serif CJK SC: © 2017–2024 Adobe (http://www.adobe.com/).

Both fonts retain their original metadata and are distributed under the SIL Open Font License 1.1. The full license is included at `src/medagent/web/fonts-OFL.txt`; exact original copyright strings, upstream URLs, versions, sizes and hashes are recorded in `src/medagent/web/font-sources.json`. CSS family aliases do not modify the font files. Loading is local and does not contact a font CDN.

The optional DSH backend executes official DeepSeek Harness packages, distributed under the MIT license. Package versions and integrity hashes are recorded in `package-lock.json`; their original license files remain in the installed packages.

Repository: https://github.com/deepseek-ai/deepseek-harness

The read-only upstream source checkout lives at `vendor/deepseek-harness` and retains its original `LICENSE` and `THIRD_PARTY_NOTICES.md`. Its inspected revision is recorded separately in `vendor/upstream.lock.json`. This project does not modify or redistribute a renamed fork of that upstream checkout.

Installed transitive dependencies retain their own licenses. The medical controller, frontend and clinical prototype content in this repository are separate project code; no production clinical validation is implied by the upstream software license.


Public development evaluation fixtures are separate from the desktop application. The fixed SWE-bench Lite DEV metadata and source archives retain their upstream attribution and licenses. Sources used across the evaluation sets include pydicom and marshmallow (MIT), astroid (LGPL; see its original LICENSE), pvlib-python (BSD-3-Clause), and SQLFluff (MIT). The pinned repository commits and archive hashes are recorded in benchmarks/swe-lite-dev*/manifest.json. Full upstream license texts remain in the source archives; they are not replaced by this notice.

The expanded evaluation's additional dependency wheels retain their original metadata and bundled license files. These wheels are extracted only into isolated evaluation workspaces, and are not added to the desktop app's production dependency graph. Public issue/test metadata and evaluation outcomes are research fixtures, not patient records or clinical validation.
