;; SPDX-License-Identifier: MPL-2.0
;; Small owned implementation of the public scalar signatures for boundary tests.
(component
  (import "report" (func (param "message" string)))
  (core module $guest
    (memory (export "memory") 1 1)
    (global $heap (mut i32) (i32.const 1024))
    (data (i32.const 8) "@RESULT@")
    (func (export "realloc") (param i32 i32 i32 i32) (result i32)
      global.get $heap
      global.get $heap local.get 3 i32.add global.set $heap)
    (func (export "activate"))
    (func (export "on-event") (param i32 i32 i32 i32) (result i32)
      i32.const 0 i32.const 8 i32.store
      i32.const 4 i32.const @LENGTH@ i32.store
      i32.const 0))
  (core instance $guest (instantiate $guest))
  (func (export "activate") (canon lift (core func $guest "activate")))
  (func (export "on-event") (param "name" string) (param "payload" string) (result string)
    (canon lift (core func $guest "on-event") (memory $guest "memory")
      (realloc (func $guest "realloc")))))
