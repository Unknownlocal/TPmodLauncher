# Private regulation transport

`RegulationChannel` is the synchronous native client for launcher-owned regulation
dispatch. Its endpoint is trusted session configuration, never mod JSON. One
authenticated loopback connection carries length-prefixed JSON frames with
sequential request IDs and an absolute action deadline. It never retries failed
requests or reconnects an expired session. An identified handler failure latches
only that owner; protocol, disconnect or timeout failures close the channel.

`framing.py` contains the bounded standard-library wire helpers. These two fixed
sources are also loaded into the native bridge's flat bootstrap namespace, so
their helper names are prefixed and package imports are conditional. This adds
no public mod method, native target selection or generic callable access. Native
validation mappings and dispatch selection are a separate trusted integration.
