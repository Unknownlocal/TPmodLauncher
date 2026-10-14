# Reduced Frida component review

Scope: the Windows x64 reduced build using the pinned 20261004 SDKs. This is
a conservative list of resolved SDK dependencies, not a claim that every
library symbol survives linking. The x86 compatibility helper must also be
covered. macOS/Linux can introduce additional components and need their own review.

The files below are exact upstream copies from commits recorded in Frida's
pinned releng/deps.toml. Their terms remain separate from the launcher's MPL.
The Frida binding/Core/Gum licence is retained in ../LICENSE.Frida.txt.

| Component | Pinned source | Preserved files |
| --- | --- | --- |
| glib | [e0cc7c6f0d88](https://github.com/frida/glib/tree/e0cc7c6f0d88f47e4dee4607df085d08c89b8a6a) | [COPYING](glib/COPYING), [LICENSES/LGPL-2.1-or-later.txt](glib/LICENSES/LGPL-2.1-or-later.txt) |
| json-glib | [1a39cbe151b0](https://github.com/frida/json-glib/tree/1a39cbe151b02c4192987c8fcc98997a59db2154) | [COPYING](json-glib/COPYING) |
| glib-networking | [ef47b1a09cf8](https://github.com/frida/glib-networking/tree/ef47b1a09cf8c1875f181bcf901643689a56d12f) | [COPYING](glib-networking/COPYING), [LICENSE_EXCEPTION](glib-networking/LICENSE_EXCEPTION) |
| libgee | [ad17ed847039](https://github.com/frida/libgee/tree/ad17ed847039469fcc2dc711ecfee2bbf7d2bf87) | [AUTHORS](libgee/AUTHORS), [COPYING](libgee/COPYING) |
| libffi | [3fe3257235cc](https://github.com/frida/libffi/tree/3fe3257235cc9ffd192e1cd567f1bdfff751fa3e) | [LICENSE](libffi/LICENSE), [LICENSE-BUILDTOOLS](libffi/LICENSE-BUILDTOOLS) |
| pcre2 | [b47486922fdc](https://github.com/frida/pcre2/tree/b47486922fdc3486499b310dc9cf903449700474) | [AUTHORS](pcre2/AUTHORS), [COPYING](pcre2/COPYING), [LICENCE](pcre2/LICENCE), [LICENSE.build](pcre2/LICENSE.build) |
| zlib | [171a3eacaea8](https://github.com/frida/zlib/tree/171a3eacaea8b731ef1fc586e7777b77742e2a1d) | [LICENSE](zlib/LICENSE), [LICENSE.build](zlib/LICENSE.build) |
| capstone | [d536b1577fd0](https://github.com/frida/capstone/tree/d536b1577fd033a31d75f48fd183aa425256cc18) | [LICENSE.TXT](capstone/LICENSE.TXT), [LICENSE_LLVM.TXT](capstone/LICENSE_LLVM.TXT) |
| xz | [e70f5800ab50](https://github.com/frida/xz/tree/e70f5800ab5001c9509d374dbf3e7e6b866c43fe) | [AUTHORS](xz/AUTHORS), [COPYING](xz/COPYING), [COPYING.GPLv2](xz/COPYING.GPLv2), [COPYING.GPLv3](xz/COPYING.GPLv3), [COPYING.LGPLv2.1](xz/COPYING.LGPLv2.1), [LICENSE.build](xz/LICENSE.build) |
| tinycc | [3da7432bebd3](https://github.com/frida/tinycc/tree/3da7432bebd348bf16cdc7a22c3717b4f22946af) | [COPYING](tinycc/COPYING) |
| openssl | [fa60a1c8c704](https://github.com/frida/openssl/tree/fa60a1c8c704e4ca0cc0dcb289c3be1fea1b50ff) | [AUTHORS.md](openssl/AUTHORS.md), [LICENSE.build](openssl/LICENSE.build), [LICENSE.txt](openssl/LICENSE.txt) |
| libsoup | [4fd67869310b](https://github.com/frida/libsoup/tree/4fd67869310b9de8fe2947bc8504ccbcf1abf285) | [AUTHORS](libsoup/AUTHORS), [COPYING](libsoup/COPYING) |
| sqlite | [ef9edc77c069](https://github.com/frida/sqlite/tree/ef9edc77c069f6824bc88352a437f00aabef6e11) | [README.md](sqlite/README.md) |
| libpsl | [b76c0fed2e27](https://github.com/frida/libpsl/tree/b76c0fed2e27353d5fbb067ecdfdf76d2281eb91) | [AUTHORS](libpsl/AUTHORS), [COPYING](libpsl/COPYING), [LICENSE](libpsl/LICENSE) |
| brotli | [01d9e2922ca8](https://github.com/frida/brotli/tree/01d9e2922ca878965ebcd71ee8965d2a7aadb47a) | [LICENSE](brotli/LICENSE) |
| nghttp2 | [ae13d24ea59c](https://github.com/frida/nghttp2/tree/ae13d24ea59c30e36ca53d1b22c4e664588d0445) | [AUTHORS](nghttp2/AUTHORS), [COPYING](nghttp2/COPYING), [LICENSE](nghttp2/LICENSE) |
| quickjs | [247bfdcbd3c5](https://github.com/frida/quickjs/tree/247bfdcbd3c58171af060351d7587541d05989fd) | [LICENSE](quickjs/LICENSE) |
| lzfse | [5cfb7c86919d](https://github.com/frida/lzfse/tree/5cfb7c86919d3c2c636d0d7552b51855a611ba1c) | [LICENSE](lzfse/LICENSE) |

## Distribution work still required

The reduced binary is not approved for distribution by this notice inventory.
In particular, the GLib family and TinyCC include LGPL terms. Shipping their
statically linked code requires the applicable licence/copyright notices and
a compliant source/relinking route. The glib-networking OpenSSL exception is
preserved, but does not remove its other LGPL requirements.

Before distributing wheels or launcher candidates:

1. Provide exact corresponding source and build material for incorporated LGPL
   code, including the pinned Frida forks and local build configuration. A table
   of external GitHub links alone is not treated as satisfying this release gate.
2. Check that recipients can rebuild/relink the native extension and embedded
   helper/agent with modified library code; retain the instructions and necessary
   build inputs. Review LGPL section 6 against the actual release arrangement.
3. Preserve notices in copied/included source files as well as top-level texts.
   Review bundled source within Frida and SDK forks for additional terms.
4. Review per-platform additions and compiler runtime distribution terms.

XZ's overview distinguishes public-domain liblzma from GPL tools/build scripts.
Those tool licences are preserved for context; their presence does not mean
the launcher binary incorporates those tools. Disabling Frida's JavaScript
SQLite API also does not prove that no other dependency links SQLite.

Primary references: [LGPL 2.1](https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html),
[Frida licence](https://github.com/frida/frida-core/blob/17.23.3/COPYING),
and the exact component sources in the table.
