# Reduced Frida distribution review

Reviewed 14 October 2026 against the pinned Frida 17.23.3 repositories and
20261004 SDKs; Frida/Microsoft licence findings updated 16 October 2026. This
records findings and remaining release work. Binary/source artifact publication
is still disabled. The local Windows installation selects the reduced
17.23.3+modlauncher.1 binding; pylock.stock.toml retains the official fallback.

## Matching source and download arrangement

For each published target/version, provide the wheel, build-details.json,
frida-sources.tar.xz and source-details.json from the same release download
location, with equal access and no additional source charge. Retain the matching
source download while offering that binary. Ship the component notices with the
runtime too. This uses the source/relinking and equivalent-download routes in
[LGPL 2.1 sections 6(a) and 6(d)](glib/LICENSES/LGPL-2.1-or-later.txt), rather
than a promise to supply source later or only links to upstream repositories.

The source archive contains the Frida code that uses the libraries as well as
the libraries, parent-pinned nested repositories, build definitions and our
rebuild tools. Restoration is offline; compilation can still need external
compiler/build-tool packages. The supplied full source can be rebuilt instead
of shipping a separate proprietary object-file kit. Windows x64 and Linux x64
relinking have both been demonstrated by changing restored TinyCC and observing
that change in the embedded agent. The 32-bit compatibility libraries are
included in the inventory but were not modified in those demonstrations.

Recipients must be able to modify the included libraries and relink them for
their own use. The pinned release builder authenticates our distributed build;
it is not the only permitted build route. The upstream scripts support local
changes, and the launcher can accept a reviewed local wheel/manifest with its
own version suffix. See [the rebuilding instructions](../../launcher/FRIDA_RELINK.md).
The game's EULA remains separate and grants no rights over these libraries.

Release preparation checks hosted GitHub provenance for all four artifacts,
their consistency and their copied bytes. No authenticated candidate has been
prepared locally yet. Hash receipts alone do not authenticate a publisher.

## Windows review

The actual Windows x64/x86 dependency records resolve 18 SDK packages. Their
exact licence files, upstream pins and source are retained. Frida's own
wxWindows exception permits additional binary uses; it does not automatically
extend to separately incorporated SDK libraries. The full GNU Library licence
text referred to by that exception is also retained in [libsoup/COPYING](libsoup/COPYING).

Separately licensed components were found outside the SDK manifests. Frida Gum tracks
Microsoft dbghelp.dll and symsrv.dll for x86, x64 and ARM64, and Core's
embed-agent.py embeds the selected debugging DLLs in the Windows binding.
Git bundles preserve all six upstream-tracked DLLs even for a Linux source
archive. No claim that those bundles contain only open-source code is valid.

The exact DLL provenance and satisfaction of the applicable distribution
conditions still need to be established before publication. Microsoft's
[DbgHelp documentation](https://learn.microsoft.com/en-us/windows/win32/debug/dbghelp-versions)
distinguishes the Debugging Tools package from the non-redistributable DLL
shipped with Windows. A Microsoft signature, or presence in Frida's repository,
does not establish which redistribution conditions apply to our archive.
The six retained DLLs have valid Microsoft signatures. The x86/x64 files report
10.0.19041.1; the ARM64 files report 10.0.26100.1. These observations identify
the files for the grant/provenance check; they do not close it.

### Frida and Microsoft licence follow-up (16 October 2026)

The pinned main Frida, Core, Gum and Python-binding COPYING files use the
wxWindows Library Licence 3.1: GNU Library GPL version 2 or later with an
exception permitting binary works based on the library to be distributed under
the distributor's own terms. That is affirmative permission for Frida's own
binary code. It does not establish Microsoft's terms for separately owned DLLs.
Gum's COPYING additionally preserves BSD notices for source-map/Closure Compiler
and regenerator; its full unchanged text is now retained in
[LICENSE.FridaGum.txt](../LICENSE.FridaGum.txt), alongside the wheel's shorter
[COPYING text](../LICENSE.Frida.txt).

The pinned ext/dbghelp and ext/symsrv directories contain DLLs and build files,
plus dbghelp.h in the former, but no separate Microsoft licence or REDIST file.
The header retains Microsoft's copyright and describes debugging support
routines as redistributable. No DLL-specific onward-distribution conditions
were found in the inspected Frida licence/readme files or wheel COPYING.
This absence is not evidence of prohibited distribution.

Microsoft's [Windows SDK REDIST list](https://learn.microsoft.com/en-us/legal/windows-sdk/redist#debugging-tools-for-windows)
expressly lists Dbghelp.dll and Symsrv.dll from Debugging Tools for Windows as
files that may be distributed as part of a program, subject to the SDK terms.
The installed 10.0.19041.0 and 10.0.26100.0 SDK licence files both contain the
Distributable Code grant and permit third-party distribution as part of the
program. An online [Microsoft copy of the SDK terms](https://github.com/microsoft/win32metadata/blob/main/licenses/sdk_license.txt)
also records those provisions. Thus an onward-distribution route exists; a new
individual permission from Microsoft is not inherently required.

Those terms also impose distribution requirements, including significant primary
functionality, preservation of notices, protective terms for distributors and
external end users, a valid program copyright notice and indemnity. They limit
the permitted target platform and prohibit applying source-disclosure or
modification-right requirements to Microsoft's code. Retain Microsoft's separate
terms rather than labelling the DLLs wxWindows or MPL.

The locally installed Debuggers x64 DLLs are version 10.0.26100.6584, so they do
not establish byte-for-byte provenance for Frida's recorded older DLLs. The
remaining check is to bind those exact files to the permitted Debugging Tools
distribution and carry the applicable terms through our release. Our launcher,
hook manager and Wasm runner have no direct DbgHelp/SymSrv use: embedding and
loading occur in upstream Frida. Our source exporter also preserves the DLLs
in upstream Git bundles. This is an outstanding evidence/packaging check, not a
finding that Frida or this project is infringing.

Also check the actual MSVC compiler/runtime terms for the statically linked
runtime and the embedded Python distribution's included runtime files.
[Microsoft's redistribution guidance](https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files?view=msvc-170)
requires applicable licence terms and the distributable-code list. Adding a
copyright notice alone does not settle this. The review did not substitute
DLLs, change Frida sources or remove objects from the pinned Git history.

## Linux review

Ubuntu 24.04/WSL native and x86 compatibility records resolve 22 SDK packages:
the Windows list plus elfutils, libbpf, libdwarf and libunwind. Their original
licence texts are retained in the component subdirectories.

Elfutils adds LGPLv3 terms for the selected library route. Its full LGPLv3 and
GPLv3 texts are retained together. Follow the Combined Works requirements in
[LGPLv3 section 4](elfutils/COPYING-LGPLV3), including the Library's corresponding
source and the application source/material needed to recombine them. The
source/rebuild route described above is the intended arrangement. Do not label
the entire elfutils repository LGPL: its utilities include GPL-covered code.
Libdwarf includes LGPL2.1/BSD code; libbpf's main library is dual licensed;
libunwind's notices are permissive. All original in-file notices remain in the
restored checkouts.

The built x64 extension's dynamic dependencies were libc, libm and the Linux
loader. No system-library binaries are copied into a release by this build.
This observation does not inventory static compiler runtime objects or the
dependencies of every embedded helper.
[GCC's runtime exception](https://gcc.gnu.org/onlinedocs/libstdc++/manual/license.html)
allows target code from eligible compilation processes to combine its runtime
with independent modules under their own terms. The local build uses GCC 13.3
without custom compiler plugins; this is the intended exception route, not a
reason to label GCC GPL code MPL. Check the final native/compatibility runtime
objects and retain their applicable exception/notices before distribution,
including any toolchain changes in hosted builds.
glibc Linux is the tested target; musl and a full Linux game adapter are not
established by this build check.

## macOS review still required

Use the existing GitHub-hosted Intel and ARM jobs on push. No self-hosted or
local macOS runners are planned, and no push was performed for this review.

The pinned Darwin backend builds reportcrash and osanalytics JavaScript through
npm. Both lock files resolve frida-objc-bridge 8.0.5 and esbuild 0.25.5.
Esbuild is a build tool; the bridge is incorporated into the generated agents.
Those agents are selected even when the separate fruity backend is disabled.
The exporter now collects the original bridge archive from its locked registry
URL and verifies its SHA-512 integrity, the actual installed lock, and all
published files against both built agents. It includes the editable source under
deps/npm/; offline restoration retains that archive unchanged. The published
JavaScript and declarations match the package's published Git commit. The
package declares LGPL-2.0 WITH WxWindows-exception-3.1 but ships no separate
licence file; the full [Library GPLv2](libsoup/COPYING) and
[wxWindows](../LICENSE.Frida.txt) texts are retained here. See the
[rebuild instructions](../../launcher/FRIDA_RELINK.md).

Both actual upstream agent targets compiled and exported successfully in a
focused Linux/WSL Meson study. This checks the agent build layout and incorporated
source, not a full macOS native build. Confirm source export on both hosted macOS
builds and review their native compiler-runtime inventory before distribution.

## Release conclusion

The source/relinking mechanism works on Windows and locally in WSL, and the
Windows/Linux notice inventory is expanded. Distribution is not cleared yet.
Close the Microsoft binary/compiler-runtime findings, confirm the macOS build
inventory and source export, confirm signed preparation in GitHub Actions, and verify the actual
four-file download arrangement before enabling artifact publication. Manual
Windows Steam acceptance remains a separate functionality check.
