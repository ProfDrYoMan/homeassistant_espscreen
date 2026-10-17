// picture_motion.h: a photo a little larger than the glass moves a pixel at a time, within its room, from a corner of
// its own toward the opposite one and back; a picture with no room stands in the middle.
#include <cassert>
#include <cstdio>
#include "components/smart_display/picture_motion.h"

using namespace picture_motion;

static void stays_within_its_room_and_moves_a_pixel_at_a_time() {
  const Path p = path(518, 518, 480, 480, "c0ffee1234");
  assert(p.range_x == 38 && p.range_y == 38);
  Offset last = at(p, 0);
  int moved = 0;
  for (uint32_t ms = 0; ms < 200u * PIXEL_MS; ms += PIXEL_MS / 3) {
    const Offset o = at(p, ms);
    assert(o.x >= 0 && o.x <= p.range_x && o.y >= 0 && o.y <= p.range_y);
    assert(o.x - last.x <= 1 && last.x - o.x <= 1 && o.y - last.y <= 1 && last.y - o.y <= 1);
    if (o.x != last.x || o.y != last.y) ++moved;
    last = o;
  }
  assert(moved > 100);  // it moves the whole time, never more than a pixel a step
}

static void it_starts_at_its_corner_and_reaches_the_far_one() {
  for (const char *mark : {"a", "b", "c", "d", "e", "f"}) {
    const Path p = path(864, 518, 800, 480, mark);
    const Offset start = at(p, 0), far = at(p, 64 * PIXEL_MS);
    assert(start.x == ((p.corner & 1) ? p.range_x : 0) && start.y == ((p.corner & 2) ? p.range_y : 0));
    assert(far.x == p.range_x - start.x && far.y == p.range_y - start.y);
    assert(at(p, 128 * PIXEL_MS).x == start.x);  // and back
  }
}

static void a_picture_without_room_stands_in_the_middle() {
  const Path p = path(480, 480, 480, 480, "x");
  assert(!p.moves() && at(p, 99999).x == 0 && at(p, 99999).y == 0);
  const Path wide = path(520, 480, 480, 480, "x");
  assert(wide.moves() && at(wide, 0).y == 0);
}

int main() {
  stays_within_its_room_and_moves_a_pixel_at_a_time();
  it_starts_at_its_corner_and_reaches_the_far_one();
  a_picture_without_room_stands_in_the_middle();
  std::puts("picture motion ok");
}
