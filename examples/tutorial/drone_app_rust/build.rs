//! Generates the C code of the project with `wireforge`, compiles it and writes the Rust bindings.
//! Needs `wireforge` on the PATH, a C compiler and libclang (for bindgen).
use std::env;
use std::path::{Path, PathBuf};
use std::process::Command;

fn wireforge(arguments: &[&str]) -> String {
    let output = Command::new("wireforge")
        .args(arguments)
        .output()
        .expect("wireforge is not on the PATH");
    assert!(
        output.status.success(),
        "wireforge failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    String::from_utf8(output.stdout).expect("wireforge printed text")
}

fn main() {
    println!("cargo:rerun-if-env-changed=DRONE_PROJECT");
    let project = env::var("DRONE_PROJECT").expect("set DRONE_PROJECT to your project");
    println!("cargo:rerun-if-changed={project}/project.yaml");
    let out = PathBuf::from(env::var("OUT_DIR").expect("set by cargo"));
    let generated = out.to_str().expect("a UTF-8 path");
    wireforge(&["generate", &project, "-o", generated, "--language", "c"]);
    let runtime = wireforge(&["runtime-dir"]);
    let runtime = Path::new(runtime.trim());
    // The MAVLink 2 model keeps its checksum code here.
    let application = Path::new(&project).join("app/include");

    cc::Build::new()
        .std("c99")
        .include(&out)
        .include(runtime)
        .include(&application)
        .file(out.join("drone/model.c"))
        .file(out.join("drone/interfaces/link.c"))
        .compile("drone_model");

    bindgen::Builder::default()
        .header(
            out.join("drone/interfaces/link.h")
                .to_str()
                .expect("a UTF-8 path"),
        )
        .clang_arg(format!("-I{generated}"))
        .clang_arg(format!("-I{}", runtime.display()))
        .clang_arg(format!("-I{}", application.display()))
        .clang_arg(format!(
            "--target={}",
            env::var("TARGET").expect("set by cargo")
        ))
        .use_core()
        .allowlist_item("drone_mavlink2_.*")
        .generate()
        .expect("bindgen could not read the generated header")
        .write_to_file(out.join("bindings.rs"))
        .expect("cannot write the bindings");
}
