<script setup lang="ts">
// The photos step's folder (app 0.4.89): a folder of Home Assistant's media (a folder under /media, Immich, Synology
// Photos, any media source). The chosen one shows as one row; Change opens the media sources one level at a time, as
// Home Assistant's own media browser does: the way back as a row of links, the folders to go into, and how many photos
// stand right in the folder that is open. Choose this folder takes it.
import { computed, ref } from "vue";
import { t } from "../i18n";
import { glyph } from "../model/topbar";
import Icon from "./ui/Icon.vue";
import { useScreensaverStore, type PhotoFolder } from "../stores/screensaver";

const screensaver = useScreensaverStore();
const T = (name: string, values?: Record<string, unknown>, plural?: number) =>
  plural === undefined ? t(`editor.screen_settings.screensaver.${name}`, values || {}) : t(`editor.screen_settings.screensaver.${name}`, values || {}, plural);
const FOLDER = "F024B";

const source = computed(() => screensaver.photos.source);
const browsing = ref(!source.value);
// The way from the media sources down to the folder that is open.
const trail = ref<{ id: string; title: string }[]>([]);
const folder = ref<PhotoFolder | null>(null);
const loading = ref(false);
let asking = 0;

async function show(id: string) {
  const mine = ++asking;
  loading.value = true;
  const found = await screensaver.browseFolder(id);
  if (mine !== asking) return;
  folder.value = found;
  loading.value = false;
}
function start() {
  browsing.value = true;
  trail.value = [{ id: "", title: T("photos_sources") }];
  void show("");
}
function into(sub: { id: string; title: string }) {
  trail.value = [...trail.value, sub];
  void show(sub.id);
}
function back(index: number) {
  trail.value = trail.value.slice(0, index + 1);
  void show(trail.value[index].id);
}
function choose() {
  if (!folder.value || !folder.value.photos) return;
  screensaver.setPhotos({ source: folder.value.id });
  browsing.value = false;
}
if (browsing.value) start();
</script>

<template>
  <div class="saver-photos">
    <div v-if="!browsing && source" class="item saver-folder" id="saver-folder">
      <span class="av mdi" aria-hidden="true">{{ glyph(FOLDER) }}</span>
      <span class="tx"><b>{{ screensaver.folderTitle(source) }}</b></span>
      <button type="button" class="btn quiet mini" id="saver-folder-change" @click="start">{{ T("photos_change") }}</button>
    </div>
    <template v-else>
      <nav class="saver-trail" :aria-label="T('photos_folder')">
        <template v-for="(step, i) in trail" :key="step.id || 'root'">
          <Icon v-if="i" name="chevron-right" class="saver-trail-sep" />
          <button type="button" class="btn link mini" :disabled="i === trail.length - 1" @click="back(i)">{{ step.title || T("photos_untitled") }}</button>
        </template>
      </nav>
      <p v-if="loading" class="hint">{{ T("photos_loading") }}</p>
      <p v-else-if="folder?.failed" class="hint warn" id="saver-folder-failed">{{ T("photos_failed") }}</p>
      <template v-else-if="folder">
        <div v-if="folder.folders.length" class="items saver-folders" role="list">
          <button v-for="sub in folder.folders" :key="sub.id" type="button" class="item" role="listitem" @click="into(sub)">
            <span class="av mdi" aria-hidden="true">{{ glyph(FOLDER) }}</span>
            <span class="tx"><b>{{ sub.title || T("photos_untitled") }}</b></span>
            <Icon name="chevron-right" />
          </button>
        </div>
        <p class="hint" id="saver-folder-count">{{ folder.photos ? T("photos_here", { n: folder.photos }, folder.photos) : folder.folders.length ? T("photos_none_here") : T("photos_empty") }}</p>
        <div class="saver-folder-keys">
          <button v-if="source" type="button" class="btn quiet" @click="browsing = false">{{ t("editor.common.cancel") }}</button>
          <button v-if="trail.length > 1" type="button" class="btn primary" id="saver-folder-choose" :disabled="!folder.photos" @click="choose">{{ T("photos_choose") }}</button>
        </div>
      </template>
    </template>
  </div>
</template>

<style scoped>
.saver-photos { display: grid; gap: 8px; }
.saver-folder { cursor: default; }
.saver-folder .av { color: var(--ink-2); }
.saver-trail { display: flex; flex-wrap: wrap; align-items: center; gap: 2px; font-size: 12px; }
.saver-trail .btn:disabled { color: var(--ink); opacity: 1; cursor: default; }
.saver-trail-sep { color: var(--muted); font-size: 14px; }
.saver-folders { max-height: 260px; overflow-y: auto; }
.saver-folders .item .av { color: var(--ink-2); }
.saver-folder-keys { display: flex; justify-content: flex-end; gap: 8px; }
</style>
