# Incorporated Darwin JavaScript package

Frida 17.23.3's Darwin backend bundles `frida-objc-bridge` 8.0.5 into its
`reportcrash` and `osanalytics` agents, including when the fruity backend is
disabled. This package is separate from the SDK component inventory.

Both upstream `package-lock.json` files identify the same published archive:

- Archive: [frida-objc-bridge-8.0.5.tgz](https://registry.npmjs.org/frida-objc-bridge/-/frida-objc-bridge-8.0.5.tgz)
- Integrity: `sha512-0xnL0VgxSQXgLj3S33S0kdAvLrCHSCIZb9HVraEgEyKnf9wOcu0KZ9fI5xzx0e0y+ry/g2Vl3yw9j3dsn92Ffg==`
- SHA-256: `dc8653034f495fbcf1d00a000fbf036cde8ffa4375baebf1876bbbec8604f1f2`
- Published source: [v8.0.5, commit a57a1e97cc46764ecf66ecc3952be169c9b2a792](https://github.com/frida/frida-objc-bridge/tree/a57a1e97cc46764ecf66ecc3952be169c9b2a792)
- Declared licence: `LGPL-2.0 WITH WxWindows-exception-3.1`, as recorded in the
  archive's `package.json`.

The published archive and matching Git commit omit a separate licence file.
The full [wxWindows licence and exception](../LICENSE.Frida.txt) and the
[GNU Library General Public Licence version 2](../frida-sdk/libsoup/COPYING)
are included in these notices. These texts retain their own original notices;
their licence-document credits do not identify authors of this bridge.

For macOS source exports, `deps/npm/frida-objc-bridge-8.0.5.tgz` preserves the
original package, including metadata and editable source. The exporter checks
its integrity against the upstream lock and compares every published file with
the files installed in each agent's actual build directory. Changed inputs or
missing agent outputs stop export. The package's JavaScript and type declarations
were also compared with the published Git commit during local review.

`esbuild` is the compiler used to produce the agents. Its package is a build
prerequisite, rather than incorporated runtime source; the upstream locks retain
its version and integrity. Neither npm nor esbuild is added to the launcher's
runtime dependencies. Full macOS native build and compiler-runtime acceptance
remain separate release checks.
