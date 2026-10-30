//! The vehicle of the WireForge tutorial in Rust, on the generated C code: MAVLink 2 over UDP, as
//! ../drone_app_c does. Sends to 127.0.0.1:14550, listens on 14551. Usage: drone_sim_rust [seconds].
use std::ffi::c_void;
use std::mem::MaybeUninit;
use std::net::UdpSocket;
use std::time::Duration;

use drone_model::*;

const ARM_DISARM: u16 = 400; // MAV_CMD_COMPONENT_ARM_DISARM
const QUADROTOR: u8 = 2; // MAV_TYPE_QUADROTOR
const ARMED: u8 = 128; // MAV_MODE_FLAG_SAFETY_ARMED
const ACTIVE: u8 = 4; // MAV_STATE_ACTIVE
const OK: wf_status = 0; // WF_OK

/// What the callbacks change; the link and the transport keep a pointer to it.
struct Vehicle {
    socket: UdpSocket,
    armed: bool,
    ground_station_seen: bool,
    ack: Option<u16>,
}

// The transport: two functions that return at once.
unsafe extern "C" fn udp_send(context: *mut c_void, bytes: *const u8, length: usize) -> usize {
    let vehicle = &*context.cast::<Vehicle>();
    let frame = std::slice::from_raw_parts(bytes, length);
    vehicle.socket.send(frame).unwrap_or(0)
}

unsafe extern "C" fn udp_receive(context: *mut c_void, buffer: *mut u8, capacity: usize) -> usize {
    let vehicle = &*context.cast::<Vehicle>();
    let bytes = std::slice::from_raw_parts_mut(buffer, capacity);
    vehicle.socket.recv(bytes).unwrap_or(0)
}

// Only the messages the vehicle acts on have callbacks; the others are decoded and ignored.
unsafe extern "C" fn on_heartbeat(context: *mut c_void, _: *const drone_mavlink2_Heartbeat) {
    (*context.cast::<Vehicle>()).ground_station_seen = true;
}

unsafe extern "C" fn on_command_long(
    context: *mut c_void,
    command: *const drone_mavlink2_CommandLong,
) {
    let vehicle = &mut *context.cast::<Vehicle>();
    let command = &*command;
    if command.command == ARM_DISARM {
        vehicle.armed = command.param1 > 0.5;
    }
    println!(
        "vehicle: COMMAND_LONG {} (param1 {}), answering COMMAND_ACK",
        command.command, command.param1
    );
    vehicle.ack = Some(command.command);
}

/// A value of a generated struct, set up by its generated `_init` function.
fn init<T>(setup: unsafe extern "C" fn(*mut T)) -> T {
    let mut value = MaybeUninit::<T>::uninit();
    unsafe {
        setup(value.as_mut_ptr());
        value.assume_init()
    }
}

fn main() -> std::io::Result<()> {
    let seconds: u32 = std::env::args()
        .nth(1)
        .and_then(|text| text.parse().ok())
        .unwrap_or(10);
    let socket = UdpSocket::bind("127.0.0.1:14551")?;
    socket.connect("127.0.0.1:14550")?;
    socket.set_nonblocking(true)?;
    let mut vehicle = Box::new(Vehicle {
        socket,
        armed: false,
        ground_station_seen: false,
        ack: None,
    });
    let context: *mut c_void = (&raw mut *vehicle).cast();

    let transport = wf_transport {
        context,
        send: Some(udp_send),
        receive: Some(udp_receive),
    };
    let mut handler: drone_mavlink2_link_handler = unsafe { std::mem::zeroed() };
    handler.context = context;
    handler.on_Heartbeat_received = Some(on_heartbeat);
    handler.on_CommandLong_received = Some(on_command_long);
    // The link holds the buffers and points to the transport and the handler, which stay in place.
    let mut link = Box::new(MaybeUninit::<drone_mavlink2_link_link>::uninit());
    let link = unsafe {
        drone_mavlink2_link_link_init(link.as_mut_ptr(), &transport, &handler);
        link.assume_init_mut()
    };
    let mut header = init(drone_mavlink2_link_header_init);
    header.systemId = 1;
    header.componentId = 1;

    println!("vehicle (Rust): MAVLink 2 on UDP, sending to 127.0.0.1:14550 for {seconds} s");
    let mut sent = 0u32;
    for tick in 0..seconds * 100 {
        let t = 0.01 * tick as f32;
        let mut count = |status: wf_status, header: &mut drone_mavlink2_link_header| {
            sent += u32::from(status == OK);
            header.sequence = header.sequence.wrapping_add(1);
        };
        unsafe {
            if tick % 100 == 0 {
                let mut heartbeat = init(drone_mavlink2_Heartbeat_init);
                heartbeat.type_ = QUADROTOR;
                heartbeat.baseMode = if vehicle.armed { ARMED } else { 0 };
                heartbeat.systemStatus = ACTIVE;
                count(
                    drone_mavlink2_link_link_send_Heartbeat(link, &heartbeat, &header),
                    &mut header,
                );
            }
            if tick % 10 == 0 {
                let mut attitude = init(drone_mavlink2_Attitude_init);
                attitude.timeBootMs = 10 * tick;
                attitude.roll = 0.2 * t.sin();
                attitude.pitch = 0.1 * t.cos();
                count(
                    drone_mavlink2_link_link_send_Attitude(link, &attitude, &header),
                    &mut header,
                );
            }
            if tick % 25 == 0 {
                let mut hud = init(drone_mavlink2_VfrHud_init);
                hud.groundspeed = if vehicle.armed { 4.5 } else { 0.0 };
                hud.alt = 10.0 + (0.5 * t).sin();
                hud.heading = (tick / 10 % 360) as i16;
                count(
                    drone_mavlink2_link_link_send_VfrHud(link, &hud, &header),
                    &mut header,
                );
            }
            drone_mavlink2_link_link_poll(link);
            if let Some(command) = (*(&raw mut *vehicle)).ack.take() {
                let mut ack = init(drone_mavlink2_CommandAck_init);
                ack.command = command;
                count(
                    drone_mavlink2_link_link_send_CommandAck(link, &ack, &header),
                    &mut header,
                );
            }
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    let seen = if vehicle.ground_station_seen {
        "seen"
    } else {
        "not seen"
    };
    println!("vehicle (Rust): done, {sent} frames sent, ground station {seen}");
    Ok(())
}
