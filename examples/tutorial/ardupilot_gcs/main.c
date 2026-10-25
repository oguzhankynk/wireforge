/* A ground station in C99 for ArduPilot, from the WireForge import of ardupilotmega.xml (every
 * message): connects to the autopilot over TCP (SITL: 127.0.0.1:5760), sends a HEARTBEAT every
 * second, asks for the telemetry streams, the AUTOPILOT_VERSION and the parameter list, and counts
 * what arrives. Usage: ardupilot_gcs [seconds] (default 20). POSIX. */
#define _POSIX_C_SOURCE 200809L

#include "ardupilot/interfaces/link.h"

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

enum {
    GCS = 6,                 /* MAV_TYPE_GCS */
    INVALID = 8,             /* MAV_AUTOPILOT_INVALID */
    REQUEST_MESSAGE = 512,   /* MAV_CMD_REQUEST_MESSAGE */
    AUTOPILOT_VERSION = 148, /* the id of AUTOPILOT_VERSION */
    ALL_STREAMS = 0,         /* MAV_DATA_STREAM_ALL */
    MY_SYSTEM = 255,
    MY_COMPONENT = 190 /* MAV_COMP_ID_MISSIONPLANNER */
};

static int tcp = -1;

static size_t tcp_send(void* context, const uint8_t* bytes, size_t length) {
    const ssize_t sent = send(tcp, bytes, length, 0);
    (void)context;
    return sent < 0 ? 0U : (size_t)sent;
}

static size_t tcp_receive(void* context, uint8_t* buffer, size_t capacity) {
    const ssize_t got = recv(tcp, buffer, capacity, 0);
    (void)context;
    return got < 0 ? 0U : (size_t)got;
}

typedef struct counts {
    unsigned heartbeats, attitudes, positions, huds, params, acks;
    unsigned version;
    float alt, roll;
    uint16_t param_count;
    uint8_t autopilot_type;
    uint32_t flight_sw;
    uint16_t acked_command;
    uint8_t ack_result;
} counts;

static void on_heartbeat(void* context, const ardupilot_mavlink_Heartbeat* message) {
    counts* seen = context;
    seen->heartbeats++;
    seen->autopilot_type = message->type;
}

static void on_attitude(void* context, const ardupilot_mavlink_Attitude* message) {
    counts* seen = context;
    seen->attitudes++;
    seen->roll = message->roll;
}

static void on_position(void* context, const ardupilot_mavlink_GlobalPositionInt* message) {
    counts* seen = context;
    (void)message;
    seen->positions++;
}

static void on_hud(void* context, const ardupilot_mavlink_VfrHud* message) {
    counts* seen = context;
    seen->huds++;
    seen->alt = message->alt;
}

static void on_param(void* context, const ardupilot_mavlink_ParamValue* message) {
    counts* seen = context;
    seen->params++;
    seen->param_count = message->paramCount;
}

static void on_version(void* context, const ardupilot_mavlink_AutopilotVersion* message) {
    counts* seen = context;
    seen->version++;
    seen->flight_sw = message->flightSwVersion;
}

static void on_ack(void* context, const ardupilot_mavlink_CommandAck* message) {
    counts* seen = context;
    seen->acks++;
    seen->acked_command = message->command;
    seen->ack_result = message->result;
}

static double now(void) {
    struct timespec t;
    (void)clock_gettime(CLOCK_MONOTONIC, &t);
    return (double)t.tv_sec + 1e-9 * (double)t.tv_nsec;
}

int main(int argc, char** argv) {
    const double seconds = argc > 1 ? atof(argv[1]) : 20.0;
    struct sockaddr_in autopilot = {0};
    autopilot.sin_family = AF_INET;
    autopilot.sin_port = htons(5760);
    autopilot.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    tcp = socket(AF_INET, SOCK_STREAM, 0);
    if (tcp < 0 || connect(tcp, (const struct sockaddr*)&autopilot, sizeof autopilot) != 0) {
        perror("ardupilot_gcs: connect to 127.0.0.1:5760");
        return 1;
    }
    (void)fcntl(tcp, F_SETFL, O_NONBLOCK);

    static counts seen;
    ardupilot_mavlink_link_handler handler = {0};
    handler.context = &seen;
    handler.on_Heartbeat_received = on_heartbeat;
    handler.on_Attitude_received = on_attitude;
    handler.on_GlobalPositionInt_received = on_position;
    handler.on_VfrHud_received = on_hud;
    handler.on_ParamValue_received = on_param;
    handler.on_AutopilotVersion_received = on_version;
    handler.on_CommandAck_received = on_ack;
    const wf_transport transport = {NULL, tcp_send, tcp_receive};
    static ardupilot_mavlink_link_link link;
    ardupilot_mavlink_link_link_init(&link, &transport, &handler);
    ardupilot_mavlink_link_header header;
    ardupilot_mavlink_link_header_init(&header);
    header.systemId = MY_SYSTEM;
    header.componentId = MY_COMPONENT;

    size_t frames = 0U, errors = 0U;
    double last_heartbeat = 0.0;
    int requested = 0;
    const double start = now();
    while (now() - start < seconds) {
        if (now() - last_heartbeat >= 1.0) {
            ardupilot_mavlink_Heartbeat heartbeat;
            ardupilot_mavlink_Heartbeat_init(&heartbeat);
            heartbeat.type = GCS;
            heartbeat.autopilot = INVALID;
            (void)ardupilot_mavlink_link_link_send_Heartbeat(&link, &heartbeat, &header);
            header.sequence++;
            last_heartbeat = now();
        }
        if (!requested && seen.heartbeats > 0U) {
            ardupilot_mavlink_RequestDataStream streams;
            ardupilot_mavlink_CommandLong command;
            ardupilot_mavlink_ParamRequestList params;
            ardupilot_mavlink_RequestDataStream_init(&streams);
            streams.targetSystem = 1U;
            streams.targetComponent = 1U;
            streams.reqStreamId = ALL_STREAMS;
            streams.reqMessageRate = 4U;
            streams.startStop = 1U;
            (void)ardupilot_mavlink_link_link_send_RequestDataStream(&link, &streams, &header);
            header.sequence++;
            ardupilot_mavlink_CommandLong_init(&command);
            command.targetSystem = 1U;
            command.targetComponent = 1U;
            command.command = REQUEST_MESSAGE;
            command.param1 = (float)AUTOPILOT_VERSION;
            (void)ardupilot_mavlink_link_link_send_CommandLong(&link, &command, &header);
            header.sequence++;
            ardupilot_mavlink_ParamRequestList_init(&params);
            params.targetSystem = 1U;
            params.targetComponent = 1U;
            (void)ardupilot_mavlink_link_link_send_ParamRequestList(&link, &params, &header);
            header.sequence++;
            requested = 1;
        }
        const wf_poll_result result = ardupilot_mavlink_link_link_poll(&link);
        frames += result.frames;
        errors += result.errors;
        const struct timespec pause = {0, 2000000L};
        (void)nanosleep(&pause, NULL);
    }
    printf("ardupilot_gcs: %zu frames decoded, %zu rejected\n", frames, errors);
    printf("ardupilot_gcs: HEARTBEAT %u (type %u), ATTITUDE %u (roll %.3f), GLOBAL_POSITION_INT "
           "%u, VFR_HUD %u (alt %.2f)\n",
           seen.heartbeats, (unsigned)seen.autopilot_type, seen.attitudes, (double)seen.roll,
           seen.positions, seen.huds, (double)seen.alt);
    printf("ardupilot_gcs: AUTOPILOT_VERSION %u (firmware %u.%u.%u), COMMAND_ACK %u (command %u, "
           "result %u)\n",
           seen.version, (unsigned)(seen.flight_sw >> 24),
           (unsigned)((seen.flight_sw >> 16) & 0xFFU), (unsigned)((seen.flight_sw >> 8) & 0xFFU),
           seen.acks, (unsigned)seen.acked_command, (unsigned)seen.ack_result);
    printf("ardupilot_gcs: PARAM_VALUE %u of %u\n", seen.params, (unsigned)seen.param_count);
    (void)close(tcp);
    return errors == 0U && seen.heartbeats > 0U && seen.version > 0U &&
                   seen.params >= seen.param_count
               ? 0
               : 1;
}
