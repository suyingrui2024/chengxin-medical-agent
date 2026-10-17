# Third-party components

The optional DSH backend executes official DeepSeek Harness packages, distributed under the MIT license. Package versions and integrity hashes are recorded in `package-lock.json`; their original license files remain in the installed packages.

Repository: https://github.com/deepseek-ai/deepseek-harness

The read-only upstream source checkout lives at `vendor/deepseek-harness` and retains its original `LICENSE` and `THIRD_PARTY_NOTICES.md`. Its inspected revision is recorded separately in `vendor/upstream.lock.json`. This project does not modify or redistribute a renamed fork of that upstream checkout.

Installed transitive dependencies retain their own licenses. The medical controller, frontend and clinical prototype content in this repository are separate project code; no production clinical validation is implied by the upstream software license.
