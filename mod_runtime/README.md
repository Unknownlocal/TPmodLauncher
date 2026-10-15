# Mod runtime architecture

`mod_runtime` owns trusted Python-in-Wasm runner artifacts independently of game
process management and native hooks. The package imports only standard-library
code; compiler and Wasmtime imports belong to the developer build command.

`bundle.py` defines the private ABI, verifies the complete bounded artifact
inventory, and returns newly checked portable bytes for one `runner.wasm`.
Source and upstream notice files accompany the artifact. The receipt provides
integrity checks; publisher authentication comes from the distribution containing
that receipt.

`build.py` builds only the fixed trusted `guest.py` against the versioned
`runner.wit`, using pinned componentize-py in temporary staging. The runner
imports the complete typed host interface. The builder checks its ABI without
executing it and publishes into a fresh directory, with the receipt written last.

`contract.py` records each host operation's required capability and scalar
signature. `launcher/runner_host.py` uses this registry to bind a separate
operation table for each mod before sandbox instantiation. Granted operations
require trusted providers; ungranted operations receive rejection callbacks.
The existing sandbox enforces import signatures, execution limits and cleanup.
Permission denial disables the affected instance before its provider executes,
including calls made during component initialization or source loading.

`guest.py` provides an in-memory source importer and a separate author-facing
`wit_world` facade. Source loading and lifecycle calls form a private runtime
protocol. The launcher retains process/session ownership and sandbox admission;
`hook_manager` retains native integration. Source manifests, player discovery,
loading budgets and startup integration are separate implementation stages. The
current component-loading path retains its existing permission admission rules.
