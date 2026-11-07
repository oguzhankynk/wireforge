#include "fixture_A/interfaces/a_b.hpp"

namespace {

// Unit A receives only Status on a_b; Command is its own message, so the handler has no callback
// for it.
class Handler final : public demo::fixture::A::a_b::Handler {
public:
    void onStatusReceived(const demo::fixture::A::Status& /*message*/) noexcept override {}
};

} // namespace

int main() {
    Handler handler;
    const demo::fixture::A::a_b::Endpoint endpoint{handler};
    return endpoint.status() == wireforge::Status::Ok ? 0 : 1;
}
