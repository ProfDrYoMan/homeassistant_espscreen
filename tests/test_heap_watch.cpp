// clang++ -std=c++17 -Wall -Wextra -Werror -I. tests/test_heap_watch.cpp -o /tmp/test_heap_watch && /tmp/test_heap_watch
//
// What asks the internal RAM for room (components/smart_display/heap_watch.h): the lowest since boot is logged the
// first time and again only once it fell another step below the last one logged.
#include "../components/smart_display/heap_watch.h"
#include <cassert>
#include <cstdio>

using namespace heap_watch;

int main() {
  LowWater water;
  assert(water.due(289000, STEP));             // the first reading is the start
  assert(!water.due(289000 - STEP + 1, STEP));  // less than a step lower: not yet
  assert(water.due(289000 - STEP, STEP));       // a whole step: logged
  assert(water.logged == 289000 - STEP);
  assert(!water.due(289000, STEP));             // the lowest never rises, and a higher reading says nothing
  assert(water.due(20000, STEP));               // a deep fall is one line, not one per step
  assert(!water.due(20000 - STEP + 1, STEP));
  printf("test_heap_watch: ok\n");
  return 0;
}
