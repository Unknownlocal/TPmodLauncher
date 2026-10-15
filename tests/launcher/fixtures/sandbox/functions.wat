;; SPDX-License-Identifier: MPL-2.0
;; Owned guest: every host interaction must use the explicitly granted bonus import.
(component
  (import "bonus" (func $bonus (param "value" s32) (result s32)))
  (core func $bonus (canon lower (func $bonus)))
  (core module $guest
    (import "host" "bonus" (func $bonus (param i32) (result i32)))
    (memory (export "memory") 1 16)
    (global $counter (mut i32) (i32.const 0))
    (func (export "apply") (param i32) (result i32) local.get 0 call $bonus)
    (func (export "counter") (result i32)
      global.get $counter i32.const 1 i32.add global.set $counter global.get $counter)
    (func (export "grow") (param i32) (result i32) local.get 0 memory.grow)
    (func (export "loop") (loop $forever (br $forever)))
    (func (export "trap") unreachable)
    (func (export "spam") (loop $forever (i32.const 0) call $bonus drop (br $forever))))
  (core instance $guest (instantiate $guest
    (with "host" (instance (export "bonus" (func $bonus))))))
  (func (export "apply") (param "value" s32) (result s32)
    (canon lift (core func $guest "apply")))
  (func (export "counter") (result s32) (canon lift (core func $guest "counter")))
  (func (export "grow") (param "pages" u32) (result s32) (canon lift (core func $guest "grow")))
  (func (export "loop") (canon lift (core func $guest "loop")))
  (func (export "trap") (canon lift (core func $guest "trap")))
  (func (export "spam") (canon lift (core func $guest "spam"))))
