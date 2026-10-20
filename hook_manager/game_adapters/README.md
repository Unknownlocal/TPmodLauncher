# Private game adapters

Game integration lives here, below the launcher-owned callable infrastructure.
Source mods cannot import these modules or select native targets. Importing a
package installs nothing; explicit trusted career initialization owns installation.

```text
hook_manager/
  callables/                    wrapper ownership, execution and restoration
  game_adapters/
    drivers/                    native driver contracts and caller behavior
    projects/                   native aero/chassis proposals and callers
    regulations/                rule selection, validator contexts and dispatch
```

`drivers` and `projects` know how the game calculates terms and which reads/writes
must stay private before an action is allowed. Their strict planners return owned
`clock`, `effective` and `proposal` data. They do not assemble regulation invocation
metadata, load mod definitions, import the regulation integration or dispatch a
mod handler. Native game regulation values can still determine native seat or
project capacity; reading those game inputs is part of mapping the game behavior.

`regulations` composes that support with adopted-rule selection and the resident
validation channel. It adds `schema`, `scope`, `operation` and `initiator`, reports
initialization incompatibility through the trusted CLI/file reporter, and binds
native guards. Driver/project support can be reused by another trusted adapter
without depending on regulation dispatch.

The direction is `regulations -> drivers/projects -> private callable/manager
infrastructure`. Internal target names use `drivers.*` and `projects.*`; they are
not mod declarations. No compatibility imports remain at the former
`hook_manager.regulations` path because this integration has not been published.

The existing `launcher/regulations` package still owns enabled-owner JSON ingest,
rule definitions and launcher dispatch; `mod_runtime/_regulations` still executes
mod Python inside Wasm. Moving host integration does not change either API or the
closed public-admission/readiness gates. The bridge's fixed source list changes
only the four transport file locations; native planner/guard activation remains
separate work. No source mod can extend that source list.

See [driver support](drivers/README.md), [project support](projects/README.md) and
[regulation integration](regulations/README.md). Developer archive generalization
remains deferred until the functional regulations framework is complete.
