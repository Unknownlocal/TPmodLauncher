# Menu example architecture

This Python source mod uses API `0.2.0` with `diagnostics` and `main-menu`
permissions. Activation registers one **Mod Launcher** button and logs a message.
The launcher supplies its identity, native rendering and click confirmation.
The button displays text without calling mod code or changing game state.

The menu adapter waits for the native menu import and checks its methods. This
example needs no career, other mod or extra `[prerequisites]` declaration. Its
button remains available at startup. The [registration prerequisites API](../../hook_manager/README.md#registration-prerequisites)
describes additional conditions for features which need them.

The mod shares the pack's Python runner. Closing that runner removes its button;
the launcher restores the native menu through the adapter.
