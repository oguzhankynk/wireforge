#include "fixture_B/interfaces/a_b.h"

/* Unit B receives Command on a_b and sends Status: the handler has a callback for Command only. */
int main(void)
{
    demo_fixture_B_a_b_handler handler = {0};
    demo_fixture_B_a_b_header header;
    demo_fixture_B_a_b_header_init(&header);
    return demo_fixture_B_a_b_dispatch_frame(&handler, (const uint8_t *)"", 0U) == WF_TRUNCATED ? 0 : 1;
}
