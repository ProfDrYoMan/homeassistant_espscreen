<script setup lang="ts">
// The build server (the add-on's build_server.py, docs/BUILDS.md "On a build server"): build firmware on a faster
// computer that runs ESPHome Desktop or ESPHome Device Builder. Not paired: where it is, then its fingerprint to compare,
// then (unless a headless server's one-time key let Tessera in at once) the wait for the person to accept Tessera on the
// server. Paired: whether it is there, whether builds go to it, what it builds now and how the last build went. Every
// state comes from the live inventory (`build_server`), so the card follows the add-on without asking.
import { computed, ref, watch } from "vue";
import { send } from "../api";
import { useBusy } from "../composables/useBusy";
import { useConfirm } from "../composables/useConfirm";
import { t } from "../i18n";
import type { BuildServerPreview, BuildServerView, Fingerprint } from "../types";
import Icon from "./ui/Icon.vue";
import UiSwitch from "./ui/UiSwitch.vue";
import { useInventoryStore } from "../stores/inventory";
import { useUiStore } from "../stores/ui";

const inv = useInventoryStore();
const ui = useUiStore();
const { confirm } = useConfirm();
const { busy, run } = useBusy();

const DOCS = "https://github.com/MaxGramser/homeassistant_espscreen/blob/main/docs/BUILDS.md#on-a-build-server";
const HEADLESS = "esphome-device-builder --remote-build-only <folder>";

const view = computed<BuildServerView | undefined>(() => inv.inventory.build_server);
const server = computed(() => view.value?.server ?? null);
const paired = computed(() => server.value?.state === "paired");
const pending = computed(() => server.value?.state === "pending");

// ---- Pairing: where, then compare, then accept on the server ----
const step = ref<"closed" | "address" | "check">("closed");
const host = ref("");
const port = ref("6055");
const key = ref("");
const found = ref<BuildServerPreview | null>(null);
const problem = ref("");

function open(again = false) {
  problem.value = "";
  found.value = null;
  key.value = "";
  if (again && server.value) { host.value = server.value.host; port.value = String(server.value.port); }
  step.value = "address";
}
async function lookUp() {
  problem.value = "";
  try {
    found.value = await run(() => send<BuildServerPreview>("build-server/preview", "POST", { host: host.value.trim(), port: port.value.trim() }));
    step.value = "check";
  } catch (e: any) { problem.value = e.message; }
}
async function pair() {
  if (!found.value) return;
  problem.value = "";
  try {
    const next = await run(() => send<BuildServerView>("build-server/pair", "POST",
      { host: found.value!.host, port: found.value!.port, key: found.value!.needs_key ? key.value.trim() : undefined }));
    step.value = "closed";
    inv.takeBuildServer(next);
    if (next.server?.state === "paired") ui.toast(t("editor.settings.build_server.paired", { name: next.server.name }));
  } catch (e: any) { problem.value = e.message; }
}
// The person accepted Tessera on the server while this page waited: say so once.
watch(() => server.value?.state, (now, before) => {
  if (before === "pending" && now === "paired") ui.toast(t("editor.settings.build_server.paired", { name: server.value!.name }));
});
async function forget(ask = true) {
  if (!server.value) return;
  if (ask && !(await confirm(t("editor.settings.build_server.forget_confirm", { name: server.value.name }), { confirm: t("editor.settings.build_server.forget_go") }))) return;
  try { inv.takeBuildServer(await run(() => send<BuildServerView>("build-server", "DELETE"))); } catch (e: any) { ui.toast(e.message); }
}
async function pairAgain() {
  const was = server.value;
  await forget(false);
  if (was) { host.value = was.host; port.value = String(was.port); }
  open();
}
async function setEnabled(enabled: boolean) {
  try { inv.takeBuildServer(await run(() => send<BuildServerView>("build-server", "PUT", { enabled }))); } catch (e: any) { ui.toast(e.message); }
}

// ---- What the card says about a paired server ----
const status = computed(() => {
  const v = view.value;
  if (!v || !server.value) return null;
  if (pending.value) return { kind: "update", text: t("editor.settings.build_server.status.pending") };
  const c = v.connection;
  if (c === "online") return v.queue && !v.queue.idle && !v.building
    ? { kind: "update", text: t("editor.settings.build_server.status.busy") }
    : { kind: "good", text: t("editor.settings.build_server.status.online") };
  if (c === "connecting" || c === "off") return { kind: "", text: t("editor.settings.build_server.status.connecting") };
  if (c === "offline") return { kind: "update", text: t("editor.settings.build_server.status.offline") };
  return { kind: "bad", text: t(`editor.settings.build_server.status.${c}`) };
});
const versionText = computed(() => {
  const v = view.value, s = server.value;
  if (!v?.esphome || !s?.esphome) return "";
  return v.esphome === s.esphome ? t("editor.settings.build_server.version_same", { version: v.esphome })
    : t("editor.settings.build_server.version_other", { ours: v.esphome, theirs: s.esphome });
});
const duration = (seconds = 0) => seconds >= 60
  ? t("editor.settings.build_server.minutes", { m: Math.floor(seconds / 60), s: seconds % 60 })
  : t("editor.settings.build_server.seconds", { s: seconds });
const buildingText = computed(() => {
  const b = view.value?.building;
  if (!b) return "";
  return t("editor.settings.build_server.building", { file: b.file, step: t(`editor.build.server.${b.phase}`, { name: server.value?.name || "" }) });
});
const lastText = computed(() => {
  const l = view.value?.last;
  if (!l || view.value?.building) return "";
  if (l.where === "here") return t("editor.settings.build_server.last_here", { file: l.file, reason: t(`editor.build.server.fallback.${l.reason}`) });
  if (l.failed) return t("editor.settings.build_server.last_failed", { file: l.file });
  return t("editor.settings.build_server.last_server", { file: l.file, time: duration(l.seconds) });
});
const names = (print: Fingerprint) => print.map((e) => e.name).join(", ");
</script>

<template>
  <section class="card build-server" id="build-server">
    <div class="bs-head">
      <h2>{{ t("editor.settings.build_server.title") }}</h2>
      <span v-if="status" id="build-server-status" class="chip status" :class="status.kind" role="status"><span class="dot"></span>{{ status.text }}</span>
    </div>

    <p v-if="!view?.available" class="hint warn">{{ t("editor.settings.build_server.unavailable") }}</p>

    <!-- Paired, or waiting for the person to accept Tessera on the server -->
    <template v-else-if="server">
      <div class="bs-server">
        <span class="bs-name">{{ server.name }}</span>
        <small>{{ server.host }}:{{ server.port }}<template v-if="versionText"> · {{ versionText }}</template></small>
      </div>

      <div v-if="pending" class="bs-wait" id="build-server-wait">
        <p><span class="spin small" aria-hidden="true"></span><b>{{ t("editor.settings.build_server.wait_title", { name: server.name }) }}</b></p>
        <p>{{ t("editor.settings.build_server.wait_text") }}</p>
        <div class="fingerprint" :aria-label="names(view!.own.emoji)"><span v-for="(e, i) in view!.own.emoji" :key="i" :title="e.name">{{ e.emoji }}</span></div>
        <small>{{ names(view!.own.emoji) }}</small>
        <div class="actions"><button type="button" class="btn quiet" :disabled="busy" @click="forget(false)">{{ t("editor.settings.build_server.cancel") }}</button></div>
      </div>

      <template v-else>
        <p v-if="view!.connection === 'offline'" class="hint">{{ t("editor.settings.build_server.offline_text") }}</p>
        <div v-else-if="view!.connection === 'refused' || view!.connection === 'mismatch'" class="bs-problem" id="build-server-problem">
          <Icon name="alert-circle-outline" />
          <span>{{ t(`editor.settings.build_server.${view!.connection}_text`, { name: server.name }) }}</span>
          <button type="button" class="btn quiet mini" :disabled="busy" @click="pairAgain">{{ t("editor.settings.build_server.pair_again") }}</button>
        </div>

        <label class="bs-switch" for="build-server-enabled">
          <span class="sr-text"><b>{{ t("editor.settings.build_server.send") }}</b><small>{{ t("editor.settings.build_server.send_hint") }}</small></span>
          <UiSwitch id="build-server-enabled" :model-value="view!.enabled" :disabled="busy || !paired" @update:model-value="setEnabled" />
        </label>

        <p v-if="buildingText" class="bs-building" id="build-server-building"><span class="spin small" aria-hidden="true"></span>{{ buildingText }}</p>
        <small v-else-if="lastText" id="build-server-last" :class="{ warn: view!.last?.where === 'here' || view!.last?.failed }">{{ lastText }}</small>

        <div class="actions"><button type="button" id="build-server-forget" class="btn quiet mini" :disabled="busy" @click="forget()">{{ t("editor.settings.build_server.forget") }}</button></div>
      </template>
    </template>

    <!-- Not paired -->
    <template v-else>
      <p>{{ t("editor.settings.build_server.intro") }}</p>
      <div v-if="step === 'closed'" class="actions">
        <button type="button" id="build-server-connect" class="btn primary" @click="open()">{{ t("editor.settings.build_server.connect") }}</button>
      </div>

      <form v-else-if="step === 'address'" class="bs-form" @submit.prevent="lookUp">
        <div class="bs-address">
          <div class="field">
            <label class="f-label" for="build-server-host">{{ t("editor.settings.build_server.address") }}</label>
            <input id="build-server-host" v-model="host" type="text" inputmode="url" autocomplete="off" spellcheck="false" placeholder="192.168.1.20" required />
          </div>
          <div class="field bs-port">
            <label class="f-label" for="build-server-port">{{ t("editor.settings.build_server.port") }}</label>
            <input id="build-server-port" v-model="port" type="text" inputmode="numeric" autocomplete="off" />
          </div>
        </div>
        <small>{{ t("editor.settings.build_server.address_hint") }}</small>
        <p v-if="problem" class="hint warn" id="build-server-problem" role="alert">{{ problem }}</p>
        <details class="bs-how">
          <summary>{{ t("editor.settings.build_server.how_title") }}</summary>
          <ul>
            <li>{{ t("editor.settings.build_server.how_desktop") }}</li>
            <li>{{ t("editor.settings.build_server.how_builder") }}</li>
            <i18n-t keypath="editor.settings.build_server.how_headless" tag="li" scope="global"><template #command><code>{{ HEADLESS }}</code></template></i18n-t>
          </ul>
          <a :href="DOCS" target="_blank" rel="noopener">{{ t("editor.settings.build_server.how_link") }}</a>
        </details>
        <div class="actions">
          <button type="submit" id="build-server-look" class="btn primary" :disabled="busy || !host.trim()"><span v-if="busy" class="spin small" aria-hidden="true"></span>{{ busy ? t("editor.settings.build_server.looking") : t("editor.settings.build_server.look_up") }}</button>
          <button type="button" class="btn quiet" :disabled="busy" @click="step = 'closed'">{{ t("editor.settings.build_server.cancel") }}</button>
        </div>
      </form>

      <form v-else-if="step === 'check' && found" class="bs-form" id="build-server-check" @submit.prevent="pair">
        <div class="bs-server">
          <span class="bs-name">{{ t("editor.settings.build_server.check_title") }}</span>
          <small>{{ t("editor.settings.build_server.check_text", { name: found.name, version: found.esphome || "?" }) }} · {{ found.host }}:{{ found.port }}</small>
        </div>
        <div class="field">
          <span class="f-label">{{ t("editor.settings.build_server.server_emoji") }}</span>
          <div class="fingerprint big" :aria-label="names(found.emoji)"><span v-for="(e, i) in found.emoji" :key="i" :title="e.name">{{ e.emoji }}</span></div>
          <small>{{ names(found.emoji) }}</small>
        </div>
        <small>{{ t("editor.settings.build_server.check_hint") }}</small>
        <div v-if="found.needs_key" class="field">
          <label class="f-label" for="build-server-key">{{ t("editor.settings.build_server.key") }}</label>
          <input id="build-server-key" v-model="key" type="text" autocomplete="off" spellcheck="false" placeholder="ABCD-EFGH-JKLM-NPQR" required />
          <small>{{ t("editor.settings.build_server.key_hint") }}</small>
        </div>
        <div v-else class="field">
          <span class="f-label">{{ t("editor.settings.build_server.own_emoji") }}</span>
          <div class="fingerprint" :aria-label="names(view!.own.emoji)"><span v-for="(e, i) in view!.own.emoji" :key="i" :title="e.name">{{ e.emoji }}</span></div>
          <small>{{ t("editor.settings.build_server.own_emoji_hint") }}</small>
        </div>
        <p v-if="problem" class="hint warn" role="alert">{{ problem }}</p>
        <div class="actions">
          <button type="submit" id="build-server-pair" class="btn primary" :disabled="busy || (found.needs_key && !key.trim())"><span v-if="busy" class="spin small" aria-hidden="true"></span>{{ busy ? t("editor.settings.build_server.pairing") : t("editor.settings.build_server.pair") }}</button>
          <button type="button" class="btn quiet" :disabled="busy" @click="step = 'address'">{{ t("editor.settings.build_server.back") }}</button>
        </div>
      </form>
    </template>
  </section>
</template>
