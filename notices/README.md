# Third-party notices

These documents preserve upstream terms; they do not relicense third-party
material under MPL-2.0.

| File | Origin and current scope |
| --- | --- |
| [LICENSE.Python.txt](LICENSE.Python.txt) | Exact copy of `LICENSE.txt` in the supplied Python 3.13.16 x64 embeddable runtime. |
| [LICENSE.Frida.txt](LICENSE.Frida.txt) | Exact match to `frida-17.23.3.dist-info/licenses/COPYING` in the downloaded, hash- and attestation-verified Windows x64 wheel. The original is retained in the prepared release package directory. |
| [LICENSE.FridaCore.txt](LICENSE.FridaCore.txt) | Exact upstream `frida-core` 17.23.3 `COPYING`, covering Core under the wxWindows Library Licence. The worker links the official Core devkit. |
| [LICENSE.FridaCore.LGPL.txt](LICENSE.FridaCore.LGPL.txt) | GNU Library General Public License version 2 text, referenced by Core's licence. |

The native devkit contains static dependencies as well as Core. A public binary
release still needs a complete dependency/source and notice review; these two
texts alone are not a claim that the release audit is complete. The devkit pins,
build instructions and current acceptance scope are in
[native/core_worker/README.md](../native/core_worker/README.md).

Project scope and the original-source licence are in [LICENCE](../LICENCE).
Instructions for independently checking the bundled Python runtime are in
[README.md](../README.md).
