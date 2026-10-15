;; SPDX-License-Identifier: MPL-2.0
;; Returning the value succeeds; the canonical post-return hook then fails.
(component
  (core module $guest
    (func (export "answer") (result i32) i32.const 42)
    (func (export "cleanup") (param i32) unreachable))
  (core instance $guest (instantiate $guest))
  (func (export "answer") (result s32)
    (canon lift (core func $guest "answer") (post-return (func $guest "cleanup")))))
