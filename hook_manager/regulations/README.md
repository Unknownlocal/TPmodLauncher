# Private regulation transport

`RegulationChannel` is the synchronous native client for launcher-owned regulation
dispatch. Its endpoint is trusted session configuration, never mod JSON. One
authenticated loopback connection carries length-prefixed JSON frames with
protocol v2, sequential request IDs and an absolute action deadline. It never retries failed
requests or reconnects an expired session. An identified handler failure latches
only that owner; protocol, disconnect or timeout failures close the channel.

`publish(native=..., custom=...)` streams a finite policy plan, bounded ordered
pages, then a seal. Native entries describe the complete adopted choices for each
changed rule; custom entries are admitted option keys. A `None` value advances
generation using an existing membership. Actions pass shared contexts/selections
and owner -> `[binding_slot, context_index, selection_index]` calls. Only
acknowledged generations can be used. `reset(epoch)` retires career data and
requires new publication. None of these operations resolve native callables.

`framing.py` bounds each frame to 256 KiB, `protocol.py` defines closed v2 shapes
and policy normalization, and `action_transfer.py` handles larger logical actions
without repeating contexts or owner tables. These fixed sources and `channel.py`
also bound/assemble multi-owner result fragments; legal Unicode refusals cannot
be forced into one frame. The action's deadline covers both directions. Sources
are also loaded into the native bridge's flat bootstrap namespace, so
their helper names are prefixed and package imports are conditional. This adds
no public mod method, native target selection or generic callable access. Native
validation mappings and dispatch selection are a separate trusted integration.

The first [project mapping slice](projects/README.md) records build-checked native
entry points and the mutation/normalization constraints for later guard wiring.
It is not yet loaded into the bridge and does not enable enforcement.
