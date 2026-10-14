# Rebuilding and relinking Frida

The separate source archive contains Git bundles for the exact Frida repositories
and SDK packages referenced by the native/compatibility builds. These bundles
contain source files, original notices and build definitions. Git history is
limited to the captured commits; shallow boundaries are preserved separately.
Local Git configuration, credentials and hooks are not copied.
Upstream-tracked binaries, including Microsoft's debugging DLLs, remain in the
Git bundles and retain their own terms. See the
[distribution findings](../notices/frida-sdk/DISTRIBUTION_REVIEW.md) before publication.

The archive also includes our build tools, recipe, developer requirements and
licence notices. Keep it available alongside the corresponding binary download.
Its `source-details.json` binds its hash to the exact `build-details.json`.
Verify both with the expected GitHub workflow/commit before using downloaded
material. A local hash receipt alone does not authenticate a publisher.

## Restore the source

Use a developer Python with `requirements-dev.txt` installed, plus Git. The
bundled embeddable interpreter lacks compiler headers. Restore into a fresh,
short directory, such as a new directory under the OS temporary folder:

```text
python launcher/frida_sources.py restore frida-sources.tar.xz --output <fresh-short-directory>
```

Restoration uses only bundled Git objects. It performs no network fetches.
The resulting `frida/` folder contains the checked-out main repository and
required submodules. `deps/src/` contains SDK package source checkouts, including
their nested submodules. Original licences and in-file copyright notices remain
in those checkouts. Git verifies the restored objects before publication.

macOS archives also retain the incorporated bridge package under `deps/npm/`.
This is the original npm archive, with editable JavaScript and type declarations,
not the minified agent output. Its version/integrity remain recorded in the
restored upstream locks. See the [package notice](../notices/frida-npm/README.md).

To modify that bridge, unpack it into a separate working directory and edit its
source. Run `npm pack` there to produce your modified package. In each restored
Darwin agent source folder (`reportcrash` and `osanalytics`), run
`npm install --save-exact <absolute-path-to-your-modified-package.tgz>` to update
its dependency and lock. The upstream agent build script then copies those
updated files and builds your package. npm and esbuild are needed for compilation;
the supplied bridge archive does not make the whole build offline.

## Modify a library and rebuild its SDK files

Set `FRIDA_DEPS` to the restored `deps/` directory. Apply your changes to a
library under `deps/src/`. Frida's pinned dependency builder reuses that checkout;
it does not discard your edits. For example, from the restored `frida/` directory:

```text
python releng/deps.py build --bundle sdk --host windows-x86_64-mt --only tinycc
```

Use `windows-x86-mt` too when changing code used by the x86 compatibility agent.
On Linux x64, use `--host linux-x86_64`, with `linux-x86` for its compatibility
library. The TinyCC/embedded-agent marker test passes on both Windows x64 and
Ubuntu 24.04 through WSL. It does not prove every dependency or macOS build.
Other libraries may have additional build dependencies; `--only` includes their
declared prerequisites. The SDK builder uses its recorded bootstrap toolchain
and may download build tools. Install the normal platform compiler prerequisites
documented in [FRIDA_BUILD.md](FRIDA_BUILD.md).

The command produces an SDK archive containing the rebuilt package and its
dependencies. Merge its package files into a separate copy of the corresponding
original SDK, preserving the other libraries. Use the original SDK/toolchain
archives identified by the binary's build details; keep production libraries
separate from this work. A partial SDK archive must not replace a complete SDK.

## Rebuild the extension and embedded agent

Use upstream's configure/compile entry points with the reduced Meson options in
`frida_build.toml`, pointing `FRIDA_DEPS` at your modified SDK directory. Keep the
native Python interpreter selection used in `build_frida.py`. Rebuilding the
whole extension also incorporates the embedded helper/agent and compatibility
assets. Use upstream's `FRIDA_EXTENSION` and `FRIDA_VERSION` packaging interface
to create your wheel; give it a distinct local package version.

The authenticated release builder deliberately requires pristine pinned inputs.
For your local changes, use the supplied upstream build scripts directly and
review/update your local wheel lock and installed-file manifest. The existing
`prepare_packages(..., refresh_manifest=True)` function can create a manifest
from your reviewed local wheel. Runtime version checking accepts a local package
suffix while checking the actual upstream Core version. There is no requirement
that a locally modified library retain our original binary hash.

This material provides a source/rebuilding route; it is not a guarantee of
byte-identical outputs or a completed licence audit for every platform. See the
[component review](../notices/frida-sdk/README.md) for remaining compiler/runtime,
additional incorporated-source and per-platform checks before distribution.
