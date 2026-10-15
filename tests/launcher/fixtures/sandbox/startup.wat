;; SPDX-License-Identifier: MPL-2.0
;; The start function runs during instantiation, before the host has a usable instance.
(component
  (import "bonus" (func $bonus (param "value" s32) (result s32)))
  (core func $bonus (canon lower (func $bonus)))
  (core module $guest
    (import "host" "bonus" (func $bonus (param i32) (result i32)))
    (func $start i32.const 0 call $bonus drop unreachable)
    (start $start))
  (core instance $guest (instantiate $guest
    (with "host" (instance (export "bonus" (func $bonus)))))))
