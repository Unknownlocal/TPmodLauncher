;; SPDX-License-Identifier: MPL-2.0
;; Tests replace the two placeholders with owned data before compilation.
(component
  (core module $guest
    (memory (export "memory") 1 1)
    (data (i32.const 8) "@TEXT@")
    (func (export "text") (result i32)
      i32.const 0 i32.const 8 i32.store
      i32.const 4 i32.const @LENGTH@ i32.store
      i32.const 0))
  (core instance $guest (instantiate $guest))
  (func (export "text") (result string)
    (canon lift (core func $guest "text") (memory $guest "memory"))))
