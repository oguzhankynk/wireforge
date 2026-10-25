/* The vehicle of the WireForge tutorial in C99 on a desktop: MAVLink 2 over UDP, as the C++ version
 * (../drone_app) does. Sends to 127.0.0.1:14550, listens on 14551. Usage: drone_sim_c [seconds]
 * (default 10). Linux and macOS (POSIX sockets). */
#define _POSIX_C_SOURCE 200809L

#include "vehicle.h"

#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

static int udp = -1;
static struct sockaddr_in ground_station;

static struct sockaddr_in loopback(uint16_t port) {
    struct sockaddr_in address = {0};
    address.sin_family = AF_INET;
    address.sin_port = htons(port);
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    return address;
}

/* The transport: every frame is one datagram; both functions return at once. */
static size_t udp_send(void* context, const uint8_t* bytes, size_t length) {
    const ssize_t sent = sendto(udp, bytes, length, 0, (const struct sockaddr*)&ground_station,
                                sizeof ground_station);
    (void)context;
    return sent < 0 ? 0U : (size_t)sent;
}

static size_t udp_receive(void* context, uint8_t* buffer, size_t capacity) {
    const ssize_t got = recv(udp, buffer, capacity, 0);
    (void)context;
    return got < 0 ? 0U : (size_t)got;
}

static void print(const char* text) {
    (void)fputs(text, stdout);
    (void)fflush(stdout);
}

int main(int argc, char** argv) {
    static vehicle drone; /* holds the link buffers: keep it off the stack */
    const long seconds = argc > 1 ? strtol(argv[1], NULL, 10) : 10L;
    const struct sockaddr_in local = loopback(14551U);
    const struct timespec tick = {0, 10L * 1000L * 1000L};
    const wf_transport transport = {NULL, udp_send, udp_receive};
    udp = socket(AF_INET, SOCK_DGRAM, 0);
    if (udp < 0 || bind(udp, (const struct sockaddr*)&local, sizeof local) != 0 ||
        fcntl(udp, F_SETFL, O_NONBLOCK) != 0) {
        (void)fputs("vehicle: cannot open UDP port 14551\n", stderr);
        return 1;
    }
    ground_station = loopback(14550U);
    vehicle_init(&drone, &transport, print);
    (void)printf("vehicle (C): MAVLink 2 on UDP, sending to 127.0.0.1:14550 for %ld s\n", seconds);
    for (long remaining = seconds * 100L; remaining > 0; --remaining) {
        vehicle_tick(&drone);
        (void)nanosleep(&tick, NULL);
    }
    (void)printf("vehicle (C): done, %lu frames sent, ground station %s\n",
                 (unsigned long)drone.frames_sent, drone.ground_station_seen ? "seen" : "not seen");
    (void)close(udp);
    return 0;
}
