<script setup lang="ts">
// The screensaver (app 0.4.48): what this screen shows in standby instead of its dimmed tiles, a player's cover, a camera
// or the clock, in the order the screen tries them. Since app 0.4.83 the card is a short list of those steps, each with
// one line of what it shows and its switch, as calm as the other cards in a narrow column; a step opens in the drawer,
// where its players, its camera or its clock are chosen. A row drags into its place, by the mouse or a finger.
// Since app 0.4.89 the photos of a folder are a step too, where the screen takes them, and a plugin the screen runs can
// add a step of its own (Add from plugins, at most four).
import { computed, onMounted } from "vue";
import { t } from "../i18n";
import { glyph } from "../model/topbar";
import { useSortableRows } from "../composables/useSortableRows";
import { text } from "../model/plugins";
import type { SaverStep } from "../types";
import Icon from "./ui/Icon.vue";
import UiMenu from "./ui/UiMenu.vue";
import UiMenuItem from "./ui/UiMenuItem.vue";
import UiSwitch from "./ui/UiSwitch.vue";
import { useTopbarStore } from "../stores/topbar";
import { PLUGIN_STEPS_MAX, SAVER_MIN_FIRMWARE, useScreensaverStore } from "../stores/screensaver";
import { useInspectorStore } from "../stores/inspector";

const topbar = useTopbarStore();
const screensaver = useScreensaverStore();
const insp = useInspectorStore();

onMounted(() => topbar.loadTopbarPreview(0));
const pictures = computed(() => Boolean(screensaver.saver?.pictures));
// The rows are the steps this screen shows; a step it does not show keeps its place in the order (setOrder).
const movable = computed(() => screensaver.saverReady && screensaver.stepsShown(null).length > 1);
const steps = useSortableRows<SaverStep>({ rows: "#screensaver-steps > .saver-row", items: () => screensaver.stepsShown(null),
  commit: (list) => screensaver.setOrder(list), skip: ".ui-switch", grip: ".grip", enabled: () => movable.value });
const order = computed(() => steps.live.value || screensaver.stepsShown(null));
// The clock stays marked while one of its entities or its + is open: they are the clock's own inspector (SaverClock.vue).
const opened = (kind: SaverStep) => (insp.inspector?.kind === "saver" && insp.inspector.step === kind)
  || (kind === "clock" && (insp.inspector?.kind === "saver-item" || insp.inspector?.kind === "saver-add"));
const line = (kind: SaverStep) => (screensaver.isOn(kind) ? screensaver.summary(kind) : { text: t("editor.screen_settings.screensaver.summary.off"), missing: false });
function open(e: MouseEvent, kind: SaverStep) {
  if (steps.click(e) || (e.target as HTMLElement).closest(".ui-switch")) return;
  insp.openSaverStep(kind);
}
</script>

<template>
  <section v-if="screensaver.saver && screensaver.saver.standby" class="set-card" id="settings-screensaver" :class="{ inactive: !screensaver.saverReady }">
    <h4><span class="mdi">{{ glyph("F04B2") }}</span>{{ t("editor.screen_settings.screensaver.title") }}</h4>
    <div class="srow setting-toggle" :class="{ inactive: !screensaver.saverReady || !screensaver.standbyOn }" data-setting="screensaver"
      @click="screensaver.saverReady && !($event.target as HTMLElement).closest('button') && screensaver.changeSaver({ show: !screensaver.saver.show })">
      <span class="s-label" id="screensaver-show-label">{{ t("editor.screen_settings.screensaver.show") }}</span>
      <div class="s-control">
        <button type="button" class="switch" role="switch" id="screensaver-show" :aria-checked="screensaver.saver.show ? 'true' : 'false'"
          aria-labelledby="screensaver-show-label" :disabled="!screensaver.saverReady" @click.stop="screensaver.changeSaver({ show: !screensaver.saver.show })"></button>
      </div>
    </div>
    <p v-if="!screensaver.saverReady" class="hint" id="screensaver-firmware">{{ t("editor.screen_settings.screensaver.needs_firmware", { version: SAVER_MIN_FIRMWARE }) }}</p>
    <p v-else-if="!screensaver.standbyOn" class="hint">{{ t("editor.screen_settings.screensaver.standby_off") }}</p>
    <template v-if="screensaver.saverReady && screensaver.saver.show">
      <p class="saver-caption">{{ pictures ? t("editor.screen_settings.screensaver.lead") : t("editor.screen_settings.screensaver.no_pictures") }}</p>
      <div class="saver-list" id="screensaver-steps" role="list" :aria-label="t('editor.screen_settings.screensaver.lead')">
        <div v-for="(kind, i) in order" :key="kind" class="saver-row" role="listitem" tabindex="0" :data-kind="kind"
          :class="{ off: !screensaver.isOn(kind), opened: opened(kind), dragging: steps.drag.value.active && steps.drag.value.index === i, still: !movable }"
          @pointerdown="steps.down($event, i)" @click="open($event, kind)" @keydown.enter.self.prevent="insp.openSaverStep(kind)" @keydown="steps.key($event, i)">
          <span class="saver-av" aria-hidden="true">
            <span class="mdi saver-glyph">{{ glyph(screensaver.stepIcon(kind)) }}</span>
            <Icon v-if="movable" name="drag-vertical" class="saver-grip" />
          </span>
          <span class="saver-tx">
            <b>{{ screensaver.stepLabel(kind) }}</b>
            <small :class="{ missing: line(kind).missing }">{{ line(kind).text }}</small>
          </span>
          <UiSwitch :model-value="screensaver.isOn(kind)" :aria-label="t('editor.screen_settings.screensaver.use', { name: screensaver.stepLabel(kind) })"
            @update:model-value="(on: boolean) => screensaver.toggleStep(kind, on)" />
          <Icon name="chevron-right" class="saver-chev" />
        </div>
      </div>
      <!-- A step of its own from a plugin this screen runs, before the clock. -->
      <UiMenu v-if="screensaver.addableSteps.length" align="start" width="240px">
        <template #trigger>
          <button type="button" class="ghost-btn" id="saver-add-step" :disabled="!screensaver.canAddStep"
            :title="screensaver.canAddStep ? undefined : t('editor.screen_settings.screensaver.plugin_steps_full', { n: PLUGIN_STEPS_MAX })">
            <Icon name="plus" />{{ t("editor.screen_settings.screensaver.plugin_add") }}</button>
        </template>
        <UiMenuItem v-for="item in screensaver.addableSteps" :key="item.key" :hint="text(item.plugin.name)" @select="screensaver.addPluginStep(item.key)">
          <span class="mdi saver-menu-glyph" aria-hidden="true">{{ glyph(item.saver.icon || item.plugin.icon) }}</span>{{ text(item.saver.name) }}
        </UiMenuItem>
      </UiMenu>
    </template>
  </section>
</template>

<style scoped>
.set-card.inactive h4 { opacity: 0.6; }
.saver-caption { margin: 10px 0 6px; color: var(--muted); font-size: 11.5px; }
/* One grouped list, as a settings app draws a list that leads further: rows apart by a hairline, the whole in one frame. */
.saver-list { display: grid; grid-template-columns: minmax(0, 1fr); border: 1px solid var(--line); border-radius: 10px; overflow: hidden; margin-bottom: 10px; }
.saver-row { display: flex; align-items: center; gap: 10px; min-width: 0; padding: 9px 8px 9px 9px; background: var(--surface); cursor: pointer; outline: none; transition: background 0.12s; }
.saver-row + .saver-row { border-top: 1px solid var(--line); }
.saver-row:hover { background: var(--surface-2); }
.saver-row:focus-visible { box-shadow: inset 0 0 0 2px var(--accent); }
.saver-row.opened { background: var(--accent-soft); }
.saver-row.dragging { background: var(--accent-soft); cursor: grabbing; position: relative; z-index: 1; box-shadow: 0 6px 18px rgba(20, 24, 40, 0.14); }
.saver-av { position: relative; width: 28px; height: 28px; border-radius: 8px; background: var(--seg); color: var(--ink-2); display: grid; place-items: center; flex: none; font-size: 16px; }
.saver-av > * { grid-area: 1 / 1; transition: opacity 0.12s; }
/* The row's icon gives way to a grip under the pointer: the row drags by it, or anywhere, without a column for it. The
   grip is an editor icon, an .mdi itself, so only the row's own glyph hides. */
.saver-grip { opacity: 0; font-size: 17px; color: var(--muted); }
@media (hover: hover) {
  .saver-row:not(.still):hover .saver-glyph { opacity: 0; }
  .saver-row:not(.still):hover .saver-grip { opacity: 1; }
}
.saver-tx { flex: 1; min-width: 0; display: grid; gap: 1px; }
.saver-tx b { font-weight: 500; font-size: 13px; color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.saver-tx small { font-size: 11.5px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.saver-tx small.missing { color: var(--accent); }
.saver-row.off .saver-av, .saver-row.off .saver-tx b { opacity: 0.5; }
.saver-menu-glyph { margin-right: 8px; color: var(--ink-2); }
#saver-add-step { margin-bottom: 10px; }
.saver-chev { flex: none; margin-left: -4px; color: var(--muted); font-size: 16px; }
</style>
