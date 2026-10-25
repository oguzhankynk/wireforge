/* The vehicle of the WireForge tutorial in C99: the part that does not depend on the transport, so
 * the desktop program (main.c, UDP) and the firmware (../drone_firmware, a UART) share it. */
#ifndef DRONE_VEHICLE_H
#define DRONE_VEHICLE_H

#include "drone/interfaces/link.h"

#include <stdbool.h>
#include <stdint.h>

typedef struct vehicle {
    drone_mavlink2_link_link link;       /* buffers, frame decoder, transport */
    drone_mavlink2_link_handler handler; /* callbacks for the messages it acts on */
    drone_mavlink2_link_header header;   /* system and component id, sequence number */
    void (*log)(const char* text);
    uint32_t ticks; /* 10 ms ticks since the start */
    bool armed;
    bool ground_station_seen;
    bool ack_pending;
    uint16_t ack_command;
    uint32_t frames_sent;
} vehicle;

/* Sets up the vehicle on @p transport; @p log prints a line of text. */
void vehicle_init(vehicle* self, const wf_transport* transport, void (*log)(const char* text));

/* Runs 10 ms of the vehicle: sends what is due (HEARTBEAT 1 Hz, ATTITUDE 10 Hz, VFR_HUD 4 Hz),
 * reads what arrived and answers a COMMAND_LONG with a COMMAND_ACK. */
void vehicle_tick(vehicle* self);

#endif /* DRONE_VEHICLE_H */
