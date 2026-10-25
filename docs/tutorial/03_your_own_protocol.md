# 3. Your own protocol

**What we will do:** turn an interface control document (ICD) table into a project, add an interface, and meet the
parts of a model. This chapter makes a second project next to `drone`; chapter 4 continues with `drone`.

## From a table
Many teams keep their messages in a spreadsheet. WireForge reads such a table as CSV or Excel (and C headers and CAN
databases, see the import guide). Save this table as `sensor.csv`:
<!-- tutorial: run -->
```sh
cat > sensor.csv <<'CSV'
section,name,id,field,type,bits,occurs,count,min,max,default,units,description
type,Vec3,,x,f32,,,,,,0.0,m/s2,X axis
type,Vec3,,y,f32,,,,,,0.0,m/s2,Y axis
type,Vec3,,z,f32,,,,,,0.0,m/s2,Z axis
message,ImuSample,0x0110,sensorId,u8,,,,1,32,1,,Sensor number
message,ImuSample,,accel,Vec3,,,,,,,,Acceleration
message,ImuSample,,temperature,f32,,,,-40,125,20,degC,Die temperature
message,Heartbeat,0x0001,uptime,u32,,,,,,,s,Seconds since start
CSV
wireforge import-icd sensor.csv sensor --name sensor --namespace demo::sensor --endian little
```
Every row is one field of a type or message; `min`, `max` and `default` become checks and initial values of the
generated code.

## Add an interface
A message alone has no frame. An interface says how messages travel: a header, a trailer and how frames are found in a
byte stream. This one has a sync word, a message id and a length, and a CRC-32 at the end:
<!-- tutorial: run -->
```sh
wireforge upsert sensor checksums crc '{ algo: crc32 }'
wireforge upsert sensor interfaces imu_link '{ version: "1.0.0", direction: rx, header: { fields: [{ name: sync, type: u16, const: 0xA55A, role: sync }, { name: msgId, type: u16, role: message_id }, { name: length, type: u8, role: payload_length }] }, trailer: { fields: [{ name: crc, type: u32, checksum: { use: crc, covers: { scope: header_and_payload } } }] }, framing: { kind: sync_length, max_frame_size: 64, resync: true }, messages: [ImuSample, Heartbeat] }'
wireforge validate sensor
wireforge encode sensor ImuSample '{ sensorId: 3, temperature: 21.5 }' --interface imu_link
```
`resync: true` lets a receiver find the next frame after garbage or a broken frame.

## A whole MAVLink dialect
MAVLink keeps its messages in XML files, the dialects: `common.xml`, ArduPilot's `ardupilotmega.xml` or your own.
`wireforge import-mavlink` turns a dialect and the files it includes into a project with every message and enum, the
fields in the order MAVLink sends them and the checksum byte of every message computed from its definition. pymavlink
ships the dialects, so take them from there:
<!-- tutorial: run -->
```sh
python -m pip install pymavlink
DIALECTS="$(python -c 'import os, pymavlink; print(os.path.dirname(pymavlink.__file__))')/dialects/v20"
wireforge import-mavlink "$DIALECTS/common.xml" mavlink --name mavlink
wireforge import-mavlink "$DIALECTS/ardupilotmega.xml" apm --name apm --message HEARTBEAT --message AHRS2
wireforge encode mavlink SysStatus '{ voltageBattery: 12600, batteryRemaining: 87 }' --interface link
```
`mavlink/` has the 210 messages of `common.xml`; `apm/` has only the two messages asked for (and the enums they
use). Both have the frame of the `drone` project, so chapter 4 works with them the same way. Import the dialect again
when it changes: the project follows it.

## The parts of a model
| Part | What it is | Example |
|------|------------|---------|
| `types` | a struct used by messages | `Vec3` |
| `messages` | what is sent, with a message id | `ImuSample` |
| `enums` | named values of an integer field | `FixType` |
| `checksums` | CRCs or your own algorithm | `crc32`, MAVLink's checksum |
| `interfaces` | header, trailer, framing and the messages of one link | `imu_link`, `link` |
| `profiles` | platform variants: defines and conditional fields | `platformA` |

Fields can be bit-fields (`bits: 3`), arrays of fixed or counted size (`occurs`), optional, conditional on a profile
(`if:`), scaled (`scale: 1.0e-7` for degrees sent as integers) or constant. The wire format is always the one you
describe: WireForge never adds bytes.

Look at the generated code of the new project:
<!-- tutorial: run -->
```sh
wireforge generate sensor -o build/sensor
```

**More:** [definition format](../SCHEMA_SPEC.md), [importers](../IMPORT.md), [examples](../GUIDE.md#examples).

**If something goes wrong:** `import-icd` reports the row of every problem. A message that does not fit
`max_frame_size` is refused with the size it needs.

Next: [4. Use the code](04_use_the_code.md)
