<script setup lang="ts">
import { nextTick, onMounted, reactive, ref } from "vue";
import { message } from "ant-design-vue";

type HistoryMessage = {
  id: number;
  role: string;
  content: string;
  tool_name: string | null;
  tool_call_id: string | null;
  tool_calls: { id: string; name: string; args: Record<string, unknown> }[] | null;
};

type LLMConfig = {
  base_url: string;
  model: string;
  api_key_set: boolean;
  local_models: string[];
};

type Stop = { period: string; name: string; category: string };
type Day = { date: string; weather_line: string; stops: Stop[] };
type ItineraryDetail = {
  itinerary_id: number;
  title: string;
  start_date: string;
  end_date: string;
  total_known_yuan: string;
  days: Day[];
};

type TextItem = { kind: "text"; key: string; role: string; text: string };
type ToolItem = {
  kind: "tool";
  key: string;
  name: string;
  args: Record<string, unknown>;
  ok: boolean | null;
  content: Record<string, unknown> | null;
  open: string[];
};
type FeedItem = TextItem | ToolItem;

const apiUp = ref<boolean | null>(null);
const feed = ref<FeedItem[]>([]);
const days = ref<Day[]>([]);
const tripTitle = ref("");
const tripMeta = ref("");
const settingsOpen = ref(false);
const settingsLoading = ref(false);
const settingsSaving = ref(false);
const sending = ref(false);
const draft = ref("");
const listEl = ref<HTMLElement | null>(null);
const llm = ref<LLMConfig | null>(null);
const form = reactive({
  base_url: "",
  model: "",
  api_key: "",
});
let localId = 0;

function nextKey(prefix: string) {
  localId += 1;
  return `${prefix}-${localId}`;
}

function safeUrl(url: unknown) {
  if (typeof url !== "string") return "";
  try {
    const parsed = new URL(url);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") return url;
  } catch {
    return "";
  }
  return "";
}

function pretty(value: unknown) {
  return JSON.stringify(value, null, 2);
}

function planId(content: Record<string, unknown> | null) {
  if (!content) return null;
  if (typeof content.itinerary_id === "number") return content.itinerary_id;
  const path = String(content.path || "");
  const matched = path.match(/(\d+)\.md$/);
  return matched ? Number(matched[1]) : null;
}

function searchRows(content: Record<string, unknown> | null) {
  const rows = content?.results;
  if (!Array.isArray(rows)) return [];
  return rows.filter((row) => row && typeof row === "object") as {
    title?: string;
    url?: string;
    snippets?: string[];
  }[];
}

async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = (data as { error?: string }).error;
    throw new Error(detail || "请求失败");
  }
  return data as T;
}

async function scrollDown() {
  await nextTick();
  if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight;
}

function textItem(role: string, text: string): TextItem {
  return { kind: "text", key: nextKey(role), role, text };
}

function toolItem(id: string, name: string, args: Record<string, unknown>): ToolItem {
  return { kind: "tool", key: id || nextKey(name), name, args, ok: null, content: null, open: ["card"] };
}

function rebuild(rows: HistoryMessage[]) {
  const items: FeedItem[] = [];
  const cards = new Map<string, ToolItem>();
  for (const row of rows) {
    if (row.role === "user") {
      items.push(textItem("user", row.content));
      continue;
    }
    if (row.role === "assistant") {
      if (row.content) items.push(textItem("assistant", row.content));
      for (const call of row.tool_calls || []) {
        const card = toolItem(call.id, call.name, call.args || {});
        items.push(card);
        cards.set(call.id, card);
      }
      continue;
    }
    if (row.role !== "tool") continue;
    let parsed: Record<string, unknown> | null = null;
    try {
      parsed = JSON.parse(row.content);
    } catch {
      parsed = { ok: false, error: row.content };
    }
    const card = row.tool_call_id ? cards.get(row.tool_call_id) : undefined;
    if (card) {
      card.ok = parsed.ok === true;
      card.content = parsed;
    } else {
      const orphan = toolItem(row.tool_call_id || "", row.tool_name || "tool", {});
      orphan.ok = parsed.ok === true;
      orphan.content = parsed;
      items.push(orphan);
    }
  }
  feed.value = items;
}

async function loadDetail(id: number) {
  const data = await apiJson<ItineraryDetail>(`/api/v1/itineraries/${id}`);
  tripTitle.value = data.title;
  tripMeta.value = `${data.start_date} 至 ${data.end_date} · ${data.total_known_yuan} 元`;
  days.value = data.days || [];
}

async function loadSettings() {
  settingsLoading.value = true;
  try {
    const data = await apiJson<LLMConfig>("/api/v1/llm/config");
    llm.value = data;
    form.base_url = data.base_url;
    form.model = data.model;
    form.api_key = "";
  } catch (err) {
    message.error(err instanceof Error ? err.message : "加载配置失败");
  } finally {
    settingsLoading.value = false;
  }
}

async function saveSettings(clearKey = false) {
  settingsSaving.value = true;
  try {
    const body: { base_url: string; model: string; api_key?: string } = {
      base_url: form.base_url.trim(),
      model: form.model.trim(),
    };
    if (clearKey) body.api_key = "";
    else if (form.api_key) body.api_key = form.api_key;
    const data = await apiJson<LLMConfig>("/api/v1/llm/config", {
      method: "PUT",
      body: JSON.stringify(body),
    });
    llm.value = data;
    form.base_url = data.base_url;
    form.model = data.model;
    form.api_key = "";
    message.success("对话模型已切换");
  } catch (err) {
    message.error(err instanceof Error ? err.message : "保存失败");
  } finally {
    settingsSaving.value = false;
  }
}

function handleFrame(frame: string) {
  let event = "message";
  let data = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data = line.slice(5).trim();
  }
  if (!data) return;
  const payload = JSON.parse(data);
  if (event === "tool_call") {
    feed.value.push(toolItem(payload.tool_call_id, payload.name, payload.args || {}));
  } else if (event === "tool_result") {
    const card = feed.value.find((item) => item.kind === "tool" && item.key === payload.tool_call_id) as
      | ToolItem
      | undefined;
    if (card) {
      card.ok = payload.ok === true;
      card.content = payload.content;
      if (card.name === "save_itinerary" && card.ok && typeof payload.content?.itinerary_id === "number") {
        loadDetail(payload.content.itinerary_id).catch(() => undefined);
      }
    }
  } else if (event === "token") {
    feed.value.push(textItem("assistant", payload.text || ""));
  } else if (event === "error") {
    feed.value.push(textItem("error", payload.message || "出错了"));
  }
}

async function readStream(body: ReadableStream<Uint8Array>) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let splitAt = buffer.indexOf("\n\n");
    while (splitAt >= 0) {
      handleFrame(buffer.slice(0, splitAt));
      buffer = buffer.slice(splitAt + 2);
      splitAt = buffer.indexOf("\n\n");
      await scrollDown();
    }
  }
}

async function send() {
  const text = draft.value.trim();
  if (!text || sending.value || apiUp.value !== true) return;
  draft.value = "";
  sending.value = true;
  feed.value.push(textItem("user", text));
  await scrollDown();
  try {
    const response = await fetch("/api/v1/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
    });
    if (!response.ok || !response.body) {
      const data = await response.json().catch(() => ({}));
      throw new Error((data as { error?: string }).error || "请求失败");
    }
    await readStream(response.body);
  } catch (err) {
    feed.value.push(textItem("error", err instanceof Error ? err.message : "请求失败"));
  } finally {
    sending.value = false;
    await scrollDown();
  }
}

function downloadPlan(id: number) {
  const link = document.createElement("a");
  link.href = `/api/v1/itineraries/${id}/document`;
  link.rel = "noopener noreferrer";
  link.click();
}

function downloadFrom(item: ToolItem) {
  const id = planId(item.content);
  if (id) downloadPlan(id);
}

onMounted(async () => {
  try {
    const health = await fetch("/api/v1/health");
    const body = await health.json();
    apiUp.value = body.ok === true;
    if (!apiUp.value) return;
    const [messageRes, itineraryRes] = await Promise.all([
      fetch("/api/v1/messages"),
      fetch("/api/v1/itineraries"),
    ]);
    rebuild((await messageRes.json()).messages);
    const itineraries = (await itineraryRes.json()).itineraries as { itinerary_id: number }[];
    if (itineraries.length) await loadDetail(itineraries[0].itinerary_id);
    await loadSettings();
    await scrollDown();
  } catch {
    apiUp.value = false;
  }
});
</script>

<template>
  <a-layout class="shell">
    <a-layout-header class="bar">
      <span>旅行规划</span>
      <span class="bar-side">
        <span>{{ sending ? "正在规划" : llm?.model ? `模型 ${llm.model}` : apiUp === false ? "连不上 8001" : "模型未设置" }}</span>
        <a-button ghost size="small" @click="settingsOpen = true">模型</a-button>
      </span>
    </a-layout-header>
    <a-modal v-model:open="settingsOpen" title="对话模型" :footer="null" @cancel="form.api_key = ''">
      <p class="hint">
        OpenAI 兼容接口。不填密钥表示保持原值，本机 Ollama 可以不填。配置留在进程内存里，重启后回到环境变量。
      </p>
      <a-descriptions bordered :column="1" size="small">
        <a-descriptions-item label="当前地址">{{ llm?.base_url || "—" }}</a-descriptions-item>
        <a-descriptions-item label="当前模型">{{ llm?.model || "未设置" }}</a-descriptions-item>
        <a-descriptions-item label="密钥">
          {{ llm ? (llm.api_key_set ? "已设置" : "未设置") : "—" }}
        </a-descriptions-item>
      </a-descriptions>
      <a-form layout="vertical" class="form" @finish="() => saveSettings(false)">
        <a-form-item label="base_url" required>
          <a-input v-model:value="form.base_url" placeholder="https://ollama.com/v1" />
        </a-form-item>
        <a-form-item label="api_key" extra="不修改请留空">
          <a-input-password v-model:value="form.api_key" placeholder="不修改请留空" />
        </a-form-item>
        <a-form-item label="model" required>
          <a-input v-model:value="form.model" placeholder="gemma4:31b-cloud" />
          <div v-if="llm?.local_models.length" class="models">
            <a-button
              v-for="name in llm.local_models"
              :key="name"
              size="small"
              @click="form.model = name"
            >
              {{ name }}
            </a-button>
          </div>
        </a-form-item>
        <a-space wrap>
          <a-button type="primary" html-type="submit" :loading="settingsSaving">应用配置</a-button>
          <a-button :loading="settingsSaving" @click="saveSettings(true)">清除密钥</a-button>
          <a-button :loading="settingsLoading" @click="loadSettings">刷新</a-button>
        </a-space>
      </a-form>
    </a-modal>
    <a-layout class="body">
      <a-layout-content class="chat">
        <div ref="listEl" class="messages">
          <a-empty v-if="feed.length === 0" description="还没有对话" />
          <div v-for="item in feed" :key="item.key" class="line">
            <template v-if="item.kind === 'text'">
              <span class="role">{{ item.role === "user" ? "你" : "助手" }}</span>
              <span class="text" :class="{ error: item.role === 'error' }">{{ item.text }}</span>
            </template>
            <a-collapse v-else v-model:activeKey="item.open" class="card">
              <a-collapse-panel key="card" :header="item.name">
                <pre>{{ pretty(item.args) }}</pre>
                <p v-if="item.ok === false" class="error">{{ item.content?.error || "失败" }}</p>
                <div v-else-if="item.name === 'web_search'">
                  <div v-for="(row, index) in searchRows(item.content)" :key="index" class="hit">
                    <a
                      v-if="safeUrl(row.url)"
                      :href="safeUrl(row.url)"
                      target="_blank"
                      rel="noopener noreferrer"
                    >{{ row.title || row.url }}</a>
                    <span v-else>{{ row.title || "无标题" }}</span>
                    <div v-for="(snippet, snippetIndex) in row.snippets || []" :key="snippetIndex" class="snippet">
                      {{ snippet }}
                    </div>
                  </div>
                </div>
                <a-button
                  v-if="item.name === 'write_plan_document' && item.ok && planId(item.content)"
                  size="small"
                  @click="downloadFrom(item)"
                >
                  下载计划
                </a-button>
                <pre v-else-if="item.content && item.name !== 'web_search'">{{ pretty(item.content) }}</pre>
              </a-collapse-panel>
            </a-collapse>
          </div>
        </div>
        <a-textarea
          v-model:value="draft"
          :rows="3"
          :disabled="apiUp !== true || sending"
          placeholder="说说想去的城市、天数和预算"
          @keydown.enter.exact.prevent="send"
        />
        <div class="send">
          <a-button type="primary" :loading="sending" :disabled="apiUp !== true" @click="send">发送</a-button>
        </div>
      </a-layout-content>
      <a-layout-sider width="380" theme="light" class="side">
        <div class="side-title">行程</div>
        <a-empty v-if="days.length === 0" description="还没有行程" />
        <template v-else>
          <div>{{ tripTitle }}</div>
          <div class="meta">{{ tripMeta }}</div>
          <div v-for="day in days" :key="day.date" class="day">
            <div class="day-title">{{ day.date.slice(5) }} {{ day.weather_line }}</div>
            <div v-for="(stop, index) in day.stops" :key="index">
              {{ stop.period }} {{ stop.name }}（{{ stop.category }}）
            </div>
          </div>
        </template>
      </a-layout-sider>
    </a-layout>
  </a-layout>
</template>

<style scoped>
.shell {
  height: 100vh;
}
.bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  color: #fff;
}
.bar-side {
  display: flex;
  align-items: center;
  gap: 12px;
}
.hint,
.meta,
.snippet {
  color: #666;
}
.form {
  margin-top: 16px;
}
.models {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}
.body {
  height: calc(100vh - 64px);
  background: #fff;
}
.chat {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
  min-height: 0;
}
.messages {
  flex: 1;
  overflow: auto;
  min-height: 0;
}
.line {
  margin-bottom: 12px;
}
.role {
  display: inline-block;
  min-width: 2.5em;
  margin-right: 8px;
  color: #666;
}
.text {
  white-space: pre-wrap;
}
.card {
  margin-top: 4px;
}
pre {
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0 0 8px;
}
.hit {
  margin-bottom: 8px;
}
.error {
  color: #a8071a;
}
.send {
  display: flex;
  justify-content: flex-end;
}
.side {
  padding: 16px;
  border-left: 1px solid #f0f0f0;
  overflow: auto;
}
.side-title,
.day-title {
  margin-bottom: 8px;
  font-weight: 600;
}
.day {
  margin-top: 16px;
}
</style>
