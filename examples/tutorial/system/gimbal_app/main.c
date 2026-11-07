/* The gimbal: it receives GimbalCommand frames from the flight computer. Here one frame is fed by hand
 * instead of a UART. */
#include "system_gimbal/interfaces/fc_gimbal.h"

#include <stdio.h>

static int pitch;
static int yaw;

static void on_command(void* context, const demo_system_gimbal_GimbalCommand* command)
{
    (void)context;
    pitch = command->pitch;
    yaw = command->yaw;
}

int main(void)
{
    /* id 0x10, length 4, pitch 10, yaw -5 (big endian) */
    const uint8_t frame[] = {0x10U, 0x04U, 0x00U, 0x0AU, 0xFFU, 0xFBU};
    demo_system_gimbal_fc_gimbal_handler handler = {0};
    handler.on_GimbalCommand_received = on_command;
    const wf_status status = demo_system_gimbal_fc_gimbal_dispatch_frame(&handler, frame, sizeof frame);
    (void)printf("gimbal: status %d, pitch %d, yaw %d\n", (int)status, pitch, yaw);
    return status == WF_OK && pitch == 10 && yaw == -5 ? 0 : 1;
}
