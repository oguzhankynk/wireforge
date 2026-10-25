/* The vehicle of the WireForge tutorial in C99; see vehicle.h. */
#include "vehicle.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

enum {
    ARM_DISARM = 400, /* MAV_CMD_COMPONENT_ARM_DISARM */
    QUADROTOR = 2,    /* MAV_TYPE_QUADROTOR */
    ARMED = 128,      /* MAV_MODE_FLAG_SAFETY_ARMED */
    ACTIVE = 4        /* MAV_STATE_ACTIVE */
};

/* Only the messages the vehicle acts on have callbacks; the others are decoded and ignored. */
static void on_heartbeat(void* context, const drone_mavlink2_Heartbeat* message) {
    vehicle* self = context;
    (void)message;
    self->ground_station_seen = true;
}

static void on_command_long(void* context, const drone_mavlink2_CommandLong* command) {
    vehicle* self = context;
    char text[96];
    if (command->command == ARM_DISARM) {
        self->armed = command->param1 > 0.5F;
    }
    (void)snprintf(text, sizeof text,
                   "vehicle: COMMAND_LONG %u (param1 %d), answering COMMAND_ACK\n",
                   (unsigned)command->command, (int)command->param1);
    self->log(text);
    self->ack_command = command->command;
    self->ack_pending = true;
}

void vehicle_init(vehicle* self, const wf_transport* transport, void (*log)(const char* text)) {
    memset(self, 0, sizeof *self);
    self->log = log;
    self->handler.context = self;
    self->handler.on_Heartbeat_received = on_heartbeat;
    self->handler.on_CommandLong_received = on_command_long;
    drone_mavlink2_link_header_init(&self->header);
    self->header.systemId = 1U;
    self->header.componentId = 1U;
    drone_mavlink2_link_link_init(&self->link, transport, &self->handler);
}

/* After every frame: the next sequence number. */
static void sent(vehicle* self, wf_status status) {
    if (status == WF_OK) {
        self->frames_sent++;
    }
    self->header.sequence = (uint8_t)(self->header.sequence + 1U);
}

void vehicle_tick(vehicle* self) {
    const float t = 0.01F * (float)self->ticks;
    if (self->ticks % 100U == 0U) {
        drone_mavlink2_Heartbeat heartbeat;
        drone_mavlink2_Heartbeat_init(&heartbeat);
        heartbeat.type = QUADROTOR;
        heartbeat.baseMode = self->armed ? ARMED : 0U;
        heartbeat.systemStatus = ACTIVE;
        sent(self, drone_mavlink2_link_link_send_Heartbeat(&self->link, &heartbeat, &self->header));
    }
    if (self->ticks % 10U == 0U) {
        drone_mavlink2_Attitude attitude;
        drone_mavlink2_Attitude_init(&attitude);
        attitude.timeBootMs = 10U * self->ticks;
        attitude.roll = 0.2F * sinf(t);
        attitude.pitch = 0.1F * cosf(t);
        sent(self, drone_mavlink2_link_link_send_Attitude(&self->link, &attitude, &self->header));
    }
    if (self->ticks % 25U == 0U) {
        drone_mavlink2_VfrHud hud;
        drone_mavlink2_VfrHud_init(&hud);
        hud.groundspeed = self->armed ? 4.5F : 0.0F;
        hud.alt = 10.0F + sinf(0.5F * t);
        hud.heading = (int16_t)((10U * self->ticks / 100U) % 360U);
        sent(self, drone_mavlink2_link_link_send_VfrHud(&self->link, &hud, &self->header));
    }
    (void)drone_mavlink2_link_link_poll(&self->link);
    if (self->ack_pending) {
        drone_mavlink2_CommandAck ack;
        drone_mavlink2_CommandAck_init(&ack);
        ack.command = self->ack_command;
        ack.result = 0U; /* MAV_RESULT_ACCEPTED; the extension fields stay 0 and are not sent */
        sent(self, drone_mavlink2_link_link_send_CommandAck(&self->link, &ack, &self->header));
        self->ack_pending = false;
    }
    self->ticks++;
}
