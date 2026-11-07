# 12. A system of units

**What we will do:** describe several programs that talk to each other in one model, generate the code of every
program separately, and see that a change in the link of two programs does not touch the files of a third one. This is
for products with more than one program (a flight computer, a ground station and a camera gimbal); nothing in chapters
1 to 11 needs it.

## One model, three units
The example is in the repository, `examples/tutorial/system/`: a flight computer `fc`, a ground station `gs` and a
`gimbal`. `fc` and `gs` talk over a radio link in both directions, `fc` sends commands to the gimbal over a UART and the
gimbal only listens. `gs` and `gimbal` have no link, so they never talk.
<!-- tutorial: run -->
```sh
cp -r wireforge-src/examples/tutorial/system system
wireforge validate system
```
The units are the `nodes` of the model, and every interface lists the units that take part in it. The direction belongs
to the message: each message of an interface says who sends it with `from`, and the other participants receive it.
`VersionQuery` is defined once and used by both interfaces:
```yaml
interfaces:
  fc_gimbal:
    version: "1.0.0"
    nodes: [fc, gimbal]
    messages:
      - { name: GimbalCommand, from: fc }
      - { name: VersionQuery, from: fc }
```
The model is split into one file per interface with `include`, so what you change in one interface stays in its file.

## The code of every unit
<!-- tutorial: run -->
```sh
wireforge generate system -o build/system
ls build/system/nodes
```
Every unit gets a folder of its own, `build/system/nodes/<unit>`, with its code, its tests and its namespace
(`demo::system::fc`, or the C prefix `demo_system_gimbal_`). A unit gets only what it takes part in: the handler of the
gimbal has a callback for `GimbalCommand` and `VersionQuery`, and none for the messages of the radio link. The messages a
unit only sends have an encoder and no callback.

To generate one unit alone, name it. Its files are the same as in the full run:
<!-- tutorial: run -->
```sh
wireforge generate system -o build/only_gimbal --node gimbal
diff -r build/system/nodes/gimbal build/only_gimbal/nodes/gimbal && echo "no difference"
```

## One unit in your firmware
The gimbal is written in C (`language: c` in the model). Its application builds with the CMake package, and
`NODE gimbal` takes the unit's code and language from the model:
```cmake
wireforge_generate(gimbal_model PROJECT "${SYSTEM_PROJECT}" NODE gimbal)
```
<!-- tutorial: run -->
```sh
cmake -S system/gimbal_app -B build/gimbal -DSYSTEM_PROJECT="$PWD/system" -DWireForge_DIR="$(wireforge cmake-dir)"
cmake --build build/gimbal
build/gimbal/gimbal
```
```text
gimbal: status 0, pitch 10, yaw -5
```

## The units together
The generated system test encodes every message with the code of its sender and decodes it with the code of every
receiver, in both languages, and compares the result with the reference codec:
<!-- tutorial: run -->
```sh
wireforge test system -o build/system-test --system | tail -n 3
```

## A change in one link
Add a field to a message of the radio link and raise the version of the interface (the extra byte is a breaking change
for the old receivers). Then ask which units are affected:
<!-- tutorial: hidden
cp -r system build/system_before
sed -i 's/      - { name: pitch, type: f32, default: 0.0 }/&\n      - { name: yaw, type: f32, default: 0.0 }/' system/messages/fc_gs.yaml
sed -i 's/    version: "1.0.0"/    version: "2.0.0"/' system/interfaces/fc_gs.yaml
-->
<!-- tutorial: run -->
```sh
wireforge compat build/system_before system --node gimbal
wireforge compat build/system_before system | tail -n 1
```
```text
affects gimbal: no
interface fc_gimbal: 1.0.0 -> 1.0.0, 0 change(s), requires none bump: OK
affected nodes: fc, gs
```
The gimbal is not affected. Its files do not change either:
<!-- tutorial: run -->
```sh
wireforge generate system -o build/system_after
diff -r build/system/nodes/gimbal build/system_after/nodes/gimbal && echo "gimbal: no difference"
```
The comments of the generated files say where each element is written (`messages/fc_gimbal.yaml:3`). Because the model
is one file per interface, a change in `fc_gs.yaml` does not move a line of the gimbal's elements. In a single-file
model a line added at the top would shift every later line number and the comments of every unit would change, though
nothing else did.

**More:** [systems of several units](../GUIDE.md#systems-of-several-units), [output layout](../CODEGEN.md#units-and-system-output),
[command line](../CLI.md).

**If something goes wrong:** `--node` and `NODE` need a model with `nodes`, and a model with `nodes` needs one of them
in the CMake package. The unit name must be one of the `nodes`; `wireforge validate` lists the problems of a model
(`from` must name a participant of the interface).

That is the end of the tutorial. The [user guide](../GUIDE.md) and the reference documents go on from here.
