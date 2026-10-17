/* Sensor node ICD as a C header (subset imported by `wireforge import-icd`). */
#ifndef SENSORS_ICD_H
#define SENSORS_ICD_H

#include <stdint.h>

#define MAX_CHANNELS 6

typedef enum {
    MODE_IDLE = 0,
    MODE_RUN,
    MODE_FAULT = 7
} Mode;

typedef struct {
    uint8_t mode : 3;   /* Mode value */
    uint8_t armed : 1;
    uint8_t spare : 4;
} Flags;

typedef struct {
    uint16_t id;
    Flags flags;
    int16_t channels[MAX_CHANNELS];
    float gain;
} SensorFrame;

struct Ping {
    uint32_t sequence;
    uint64_t timestamp; // microseconds
};

#endif
