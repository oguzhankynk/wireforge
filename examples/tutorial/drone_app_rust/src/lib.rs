//! The generated C code of the project as Rust declarations. It needs neither the standard library
//! nor a heap, so this crate also builds for a microcontroller.
#![no_std]
#![allow(
    non_camel_case_types,
    non_snake_case,
    non_upper_case_globals,
    dead_code
)]

include!(concat!(env!("OUT_DIR"), "/bindings.rs"));
