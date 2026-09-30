<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from "vue";
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
type Leg = {
  from_name: string;
  to_name: string;
  mode: string;
  minutes: number;
  kilometers: number;
};
type Day = { date: string; weather_line: string; stops: Stop[]; legs?: Leg[] };
type BudgetLine = {
  name: string;
  price_yuan?: string;
  quantity?: number;
  source_url?: string | null;
};
type SourceLine = { title?: string; url?: string };
type ItineraryDetail = {
  itinerary_id: number;
  title: string;
  cities: string[];
  people: number;
  start_date: string;
  end_date: string;
  total_known_yuan: string;
  days: Day[];
  budget_items: BudgetLine[];
  unknown_items: unknown[];
  sources: SourceLine[];
  has_document: boolean;
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

const SAMPLE =
  "下周五起，两个人去青岛玩三天，想看海和博物馆，酒店一晚 400，预算 3000。排好之后给我一份计划文档。";

const TOOL_LABEL: Record<string, string> = {
  geocode: "地点",
  get_weather: "天气",
  search_places: "附近",
  web_search: "搜索",
  estimate_route: "路程",
  estimate_budget: "费用",
  save_itinerary: "保存行程",
  write_plan_document: "计划文档",
  list_itineraries: "已保存",
};

const apiUp = ref<boolean | null>(null);
const feed = ref<FeedItem[]>([]);
const trip = ref<ItineraryDetail | null>(null);
const settingsOpen = ref(false);
const settingsLoading = ref(false);
const settingsSaving = ref(false);
const sending = ref(false);
const draft = ref("");
const listEl = ref<HTMLElement | null>(null);
const draftBox = ref<{ focus: () => void } | null>(null);
const llm = ref<LLMConfig | null>(null);
const form = reactive({
  base_url: "",
  model: "",
  api_key: "",
});
let localId = 0;

const days = computed(() => trip.value?.days || []);
const statusText = computed(() => {
  if (sending.value) return "正在规划";
  if (apiUp.value === false) return "连不上 8001";
  if (llm.value?.model) return llm.value.model;
  if (apiUp.value === null) return "连接中";
  return "模型未设置";
});
const statusClass = computed(() => {
  if (sending.value) return "busy";
  if (apiUp.value === false) return "down";
  if (apiUp.value === true) return "live";
  return "";
});

function nextKey(prefix: string) {
  localId += 1;
  return `${prefix}-${localId}`;
}

function textOf(value: unknown) {
  if (value === null || value === undefined) return "";
  return String(value);
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

function asRecords(value: unknown) {
  if (!Array.isArray(value)) return [];
  return value.filter((row) => row && typeof row === "object") as Record<string, unknown>[];
}

function toolLabel(name: string) {
  return TOOL_LABEL[name] || name;
}

function statusLabel(item: ToolItem) {
  if (item.ok === null) return "进行中";
  if (item.ok) return "完成";
  return "失败";
}

function errorText(content: Record<string, unknown> | null) {
  const value = content?.error;
  return typeof value === "string" && value ? value : "失败";
}

function previewText(content: Record<string, unknown> | null) {
  const value = content?.preview;
  return typeof value === "string" ? value : "";
}

function formatKm(value: unknown) {
  const number = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(number)) return "";
  return (Math.round(number * 10) / 10).toFixed(1);
}

function coord(value: unknown) {
  const number = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(number)) return "";
  return number.toFixed(2);
}

function placeMeta(row: Record<string, unknown>) {
  const latitude = coord(row.latitude);
  const point = latitude ? `${latitude}, ${coord(row.longitude)}` : "";
  return [textOf(row.country), point].filter(Boolean).join(" · ");
}

function modeLabel(mode: unknown) {
  if (mode === "driving") return "驾车";
  if (mode === "foot") return "步行";
  return textOf(mode);
}

function dayLabel(date: string) {
  const parsed = new Date(`${date}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return date;
  return `${date.slice(5)} 周${"日一二三四五六"[parsed.getDay()]}`;
}

function argLine(item: ToolItem) {
  const args = item.args;
  if (item.name === "geocode") return textOf(args.name);
  if (item.name === "get_weather") return `${textOf(args.start_date)} · ${textOf(args.days)} 天`;
  if (item.name === "search_places") return textOf(args.category);
  if (item.name === "web_search") return textOf(args.query);
  if (item.name === "estimate_route") return `${textOf(args.from_name)} → ${textOf(args.to_name)}`;
  if (item.name === "estimate_budget") return `${textOf(args.people)} 人 · ${textOf(args.nights)} 晚`;
  if (item.name === "save_itinerary") return textOf(args.title);
  if (item.name === "write_plan_document" && args.itinerary_id) return `#${textOf(args.itinerary_id)}`;
  return "";
}

function planId(content: Record<string, unknown> | null) {
  if (!content) return null;
  if (typeof content.itinerary_id === "number") return content.itinerary_id;
  const path = String(content.path || "");
  const matched = path.match(/(\d+)\.md$/);
  return matched ? Number(matched[1]) : null;
}

function searchRows(content: Record<string, unknown> | null) {
  return asRecords(content?.results).map((row) => ({
    title: textOf(row.title),
    url: row.url,
    snippets: Array.isArray(row.snippets) ? row.snippets.map((item) => textOf(item)).filter(Boolean) : [],
  }));
}

function legAfter(day: Day, index: number) {
  const leg = day.legs?.[index];
  if (!leg) return "";
  const km = formatKm(leg.kilometers);
  return `${leg.from_name} → ${leg.to_name} · ${modeLabel(leg.mode)} ${leg.minutes} 分钟${km ? ` · ${km} 公里` : ""}`;
}

function unknownName(item: unknown) {
  if (typeof item === "string") return item;
  if (item && typeof item === "object" && "name" in item) return textOf(item.name);
  return "";
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

function parseTool(content: string): Record<string, unknown> {
  try {
    const parsed = JSON.parse(content);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed;
  } catch {
    /* 工具正文不是 JSON */
  }
  return { ok: false, error: content };
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
    const parsed = parseTool(row.content);
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
  trip.value = await apiJson<ItineraryDetail>(`/api/v1/itineraries/${id}`);
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
      const savedId =
        (card.name === "save_itinerary" || card.name === "write_plan_document") && card.ok
          ? planId(payload.content)
          : null;
      if (savedId) loadDetail(savedId).catch(() => undefined);
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

function onComposerEnter(event: KeyboardEvent) {
  if (event.isComposing || event.keyCode === 229) return;
  event.preventDefault();
  void send();
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

async function useSample() {
  draft.value = SAMPLE;
  await nextTick();
  draftBox.value?.focus();
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
  <a-layout class="shell" :style="{ background: '#12262c', minHeight: '100vh' }">
    <a-layout-header
      class="top"
      :style="{ background: '#12262c', height: '72px', lineHeight: 'normal', padding: '0 22px' }"
    >
      <div class="brand">
        <span class="mark">旅</span>
        <div>
          <div class="brand-name">旅行规划</div>
          <div class="brand-sub">坐标、天气和金额只来自工具</div>
        </div>
      </div>
      <div class="top-actions">
        <span class="status" :class="statusClass"><i></i>{{ statusText }}</span>
        <a-button class="model-btn" @click="settingsOpen = true">模型</a-button>
      </div>
    </a-layout-header>

    <a-modal
      v-model:open="settingsOpen"
      wrap-class-name="trip-modal"
      title="对话模型"
      :footer="null"
      :width="480"
      @cancel="form.api_key = ''"
    >
      <p class="modal-hint">
        OpenAI 兼容接口。不填密钥表示保持原值，本机 Ollama 可以不填。配置留在进程内存里，重启后回到环境变量。
      </p>
      <dl class="now">
        <div>
          <dt>当前地址</dt>
          <dd>{{ llm?.base_url || "—" }}</dd>
        </div>
        <div>
          <dt>当前模型</dt>
          <dd>{{ llm?.model || "未设置" }}</dd>
        </div>
        <div>
          <dt>密钥</dt>
          <dd>{{ llm ? (llm.api_key_set ? "已设置" : "未设置") : "—" }}</dd>
        </div>
      </dl>
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
            <button
              v-for="name in llm.local_models"
              :key="name"
              type="button"
              class="chip"
              @click="form.model = name"
            >
              {{ name }}
            </button>
          </div>
        </a-form-item>
        <div class="modal-actions">
          <a-button type="primary" html-type="submit" :loading="settingsSaving">应用配置</a-button>
          <a-button :loading="settingsSaving" @click="saveSettings(true)">清除密钥</a-button>
          <a-button :loading="settingsLoading" @click="loadSettings">刷新</a-button>
        </div>
      </a-form>
    </a-modal>

    <a-layout class="body" :style="{ background: 'transparent' }">
      <a-layout-content class="chat">
        <div ref="listEl" class="messages">
          <div v-if="feed.length === 0" class="empty">
            <p class="empty-kicker">{{ apiUp === false ? "后端没接上" : "从一句话开始" }}</p>
            <h2 v-if="apiUp === false">先在仓库根目录运行 ./start.sh</h2>
            <template v-else>
              <h2>说说城市、天数和预算。</h2>
              <button type="button" class="sample" @click="useSample">{{ SAMPLE }}</button>
            </template>
          </div>

          <div v-for="item in feed" :key="item.key" class="line" :class="item.kind === 'text' ? item.role : 'tool'">
            <template v-if="item.kind === 'text'">
              <div class="who">{{ item.role === "user" ? "你" : item.role === "error" ? "出错" : "助手" }}</div>
              <div class="bubble" :class="item.role">{{ item.text }}</div>
            </template>

            <a-collapse v-else v-model:activeKey="item.open" class="card" :bordered="false">
              <a-collapse-panel key="card">
                <template #header>
                  <div class="tool-head">
                    <span class="tool-kicker">{{ toolLabel(item.name) }}</span>
                    <span class="tool-arg">{{ argLine(item) }}</span>
                    <span class="pill" :class="item.ok === false ? 'bad' : item.ok ? 'ok' : 'wait'">
                      {{ statusLabel(item) }}
                    </span>
                  </div>
                </template>

                <p v-if="item.ok === false" class="fail">{{ errorText(item.content) }}</p>
                <p v-else-if="item.ok === null" class="quiet">正在查询，结果会写在这张卡上。</p>
                <template v-else-if="item.name === 'geocode'">
                  <p v-if="item.content?.needs_user_choice" class="quiet">有多个候选，选一个再继续。</p>
                  <ul class="facts">
                    <li v-for="(row, index) in asRecords(item.content?.results)" :key="index">
                      <strong>{{ row.name }}</strong>
                      <span>{{ placeMeta(row) }}</span>
                    </li>
                  </ul>
                </template>
                <ul v-else-if="item.name === 'get_weather'" class="facts">
                  <li v-for="(row, index) in asRecords(item.content?.days)" :key="index">
                    <strong>{{ textOf(row.date).slice(5) }}</strong>
                    <span>{{ row.weather_line }}</span>
                  </li>
                </ul>
                <ul v-else-if="item.name === 'search_places'" class="facts">
                  <li v-for="(row, index) in asRecords(item.content?.results)" :key="index">
                    <strong>{{ row.name }}</strong>
                  </li>
                </ul>
                <div v-else-if="item.name === 'web_search'">
                  <div v-for="(row, index) in searchRows(item.content)" :key="index" class="hit">
                    <a
                      v-if="safeUrl(row.url)"
                      :href="safeUrl(row.url)"
                      target="_blank"
                      rel="noopener noreferrer"
                    >{{ row.title || row.url }}</a>
                    <span v-else class="hit-title">{{ row.title || "无标题" }}</span>
                    <div v-for="(snippet, snippetIndex) in row.snippets" :key="snippetIndex" class="snippet">
                      {{ snippet }}
                    </div>
                  </div>
                </div>
                <div v-else-if="item.name === 'estimate_route' && item.content" class="route">
                  <div class="route-ends">
                    <span>{{ item.content.from }}</span>
                    <em>→</em>
                    <span>{{ item.content.to }}</span>
                  </div>
                  <p>{{ modeLabel(item.content.mode) }} · {{ item.content.minutes }} 分钟 · {{ formatKm(item.content.kilometers) }} 公里</p>
                </div>
                <div v-else-if="item.name === 'estimate_budget' && item.content" class="money">
                  <div class="money-total">{{ item.content.total_known_yuan }}<small>元</small></div>
                  <ul class="facts">
                    <li v-for="(row, index) in asRecords(item.content.items)" :key="index">
                      <strong>{{ row.name }}</strong>
                      <span>{{ row.price_yuan }} 元 × {{ row.quantity }}</span>
                    </li>
                  </ul>
                  <p v-for="(row, index) in asRecords(item.content.unknown)" :key="`u-${index}`" class="quiet">
                    未计入 {{ row.name }}
                  </p>
                </div>
                <p v-else-if="item.name === 'save_itinerary'" class="quiet">
                  已保存行程 #{{ planId(item.content) }}
                </p>
                <div v-else-if="item.name === 'write_plan_document'">
                  <pre v-if="previewText(item.content)" class="preview">{{ previewText(item.content) }}</pre>
                  <a-button
                    v-if="planId(item.content)"
                    type="primary"
                    size="small"
                    @click="downloadFrom(item)"
                  >
                    下载计划
                  </a-button>
                </div>
                <ul v-else-if="item.name === 'list_itineraries'" class="facts">
                  <li v-for="(row, index) in asRecords(item.content?.itineraries)" :key="index">
                    <strong>{{ row.title }}</strong>
                    <span>{{ textOf(row.start_date).slice(5) }} 至 {{ textOf(row.end_date).slice(5) }} · {{ row.total_known_yuan }} 元</span>
                  </li>
                </ul>
                <p v-else class="quiet">已返回</p>
              </a-collapse-panel>
            </a-collapse>
          </div>
        </div>

        <div class="composer">
          <a-textarea
            ref="draftBox"
            v-model:value="draft"
            :auto-size="{ minRows: 2, maxRows: 6 }"
            :disabled="apiUp !== true || sending"
            placeholder="说说想去的城市、天数和预算"
            @keydown.enter.exact="onComposerEnter"
          />
          <div class="composer-bar">
            <span>{{ sending ? "工具会一个一个回来，整段常常要一两分钟。" : "Enter 发送，Shift+Enter 换行" }}</span>
            <a-button type="primary" :loading="sending" :disabled="apiUp !== true" @click="send">
              {{ sending ? "规划中" : "发送" }}
            </a-button>
          </div>
        </div>
      </a-layout-content>

      <a-layout-sider
        class="side"
        :width="400"
        :style="{ background: '#12262c', overflow: 'auto' }"
      >
        <div v-if="!trip" class="side-empty">
          <div class="ticket">
            <span>尚未成行</span>
            <strong>行程会写在这里</strong>
            <p>确认保存之后，按天列出天气和站点。</p>
          </div>
        </div>
        <div v-else class="book">
          <div class="cover">
            <div class="cover-kicker">行程 #{{ trip.itinerary_id }}</div>
            <h2>{{ trip.title }}</h2>
            <div class="tags">
              <span v-for="city in trip.cities" :key="city">{{ city }}</span>
              <span>{{ trip.people }} 人</span>
            </div>
            <p class="cover-dates">{{ trip.start_date }} 至 {{ trip.end_date }}</p>
            <div class="cover-sum">
              <span>已知费用</span>
              <strong>{{ trip.total_known_yuan }}</strong>
              <em>元</em>
            </div>
            <a-button v-if="trip.has_document" class="download" @click="downloadPlan(trip.itinerary_id)">
              下载计划
            </a-button>
          </div>

          <article v-for="(day, index) in days" :key="day.date" class="day">
            <div class="day-index">{{ String(index + 1).padStart(2, "0") }}</div>
            <div class="day-body">
              <h3>{{ dayLabel(day.date) }}</h3>
              <p class="weather">{{ day.weather_line }}</p>
              <template v-for="(stop, stopIndex) in day.stops" :key="stopIndex">
                <div class="stop">
                  <span class="period">{{ stop.period }}</span>
                  <div>
                    <div class="stop-name">{{ stop.name }}</div>
                    <div class="stop-cat">{{ stop.category }}</div>
                  </div>
                </div>
                <div v-if="legAfter(day, stopIndex)" class="leg">{{ legAfter(day, stopIndex) }}</div>
              </template>
            </div>
          </article>

          <section v-if="trip.budget_items.length || trip.unknown_items.length" class="ledger">
            <h3>费用</h3>
            <div v-for="(item, index) in trip.budget_items" :key="index" class="ledger-row">
              <span>{{ item.name }}</span>
              <span>{{ item.price_yuan }} × {{ item.quantity }}</span>
            </div>
            <p v-for="(item, index) in trip.unknown_items" :key="`unknown-${index}`" class="unknown">
              未计入 {{ unknownName(item) }}
            </p>
          </section>

          <section v-if="trip.sources.length" class="sources">
            <h3>来源</h3>
            <div v-for="(source, index) in trip.sources" :key="index">
              <a
                v-if="safeUrl(source.url)"
                :href="safeUrl(source.url)"
                target="_blank"
                rel="noopener noreferrer"
              >{{ source.title || source.url }}</a>
              <span v-else>{{ source.title || "无标题" }}</span>
            </div>
          </section>
          <p class="side-note">不订票。带链接的价格必须对得上这次搜到的原链接。</p>
        </div>
      </a-layout-sider>
    </a-layout>
  </a-layout>
</template>

<style scoped>
.shell {
  height: 100vh;
  color: #f4f1ea;
}

.top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: #f4f1ea;
  border-bottom: 1px solid rgba(244, 241, 234, 0.08);
}

.brand,
.top-actions,
.composer-bar,
.tool-head,
.route-ends,
.cover-sum,
.tags,
.modal-actions,
.models {
  display: flex;
  align-items: center;
}

.brand {
  gap: 12px;
  min-width: 0;
}

.mark {
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  border: 1px solid rgba(198, 161, 91, 0.7);
  border-radius: 10px;
  color: #e7d3a4;
  font-family: "Noto Serif CJK SC", "Noto Serif CJK JP", serif;
  font-size: 18px;
}

.brand-name {
  font-family: "Noto Serif CJK SC", "Noto Serif CJK JP", serif;
  font-size: 18px;
  letter-spacing: 0.04em;
}

.brand-sub,
.status,
.composer-bar,
.tool-arg,
.quiet,
.snippet,
.stop-cat,
.weather,
.leg,
.side-note,
.cover-dates,
.modal-hint {
  color: #8d8478;
}

.brand-sub {
  margin-top: 2px;
  color: rgba(244, 241, 234, 0.62);
  font-size: 12px;
}

.top-actions {
  gap: 14px;
  min-width: 0;
}

.status {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  max-width: 240px;
  color: rgba(244, 241, 234, 0.78);
  font-size: 13px;
}

.status i {
  width: 8px;
  height: 8px;
  flex: none;
  border-radius: 50%;
  background: #8d9a90;
}

.status.live i,
.status.busy i {
  background: #d7b56a;
}

.status.busy i {
  animation: pulse 1.4s ease-in-out infinite;
}

@keyframes pulse {
  50% {
    opacity: 0.35;
  }
}

.status.down i {
  background: #e07a68;
}

.status {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.model-btn {
  height: 34px;
  color: #f4f1ea !important;
  background: transparent !important;
  border-color: rgba(244, 241, 234, 0.28) !important;
}

.model-btn:hover {
  color: #e7d3a4 !important;
  border-color: #c6a15b !important;
}

.body {
  min-height: 0;
}

.chat {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  background:
    radial-gradient(900px 420px at 0% 0%, rgba(198, 161, 91, 0.18), transparent 55%),
    #f3efe6;
}

.messages {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 28px 8vw 12px;
}

.empty {
  max-width: 640px;
  margin: 10vh auto 0;
}

.empty-kicker,
.cover-kicker,
.tool-kicker {
  color: #8a6232;
  font-size: 12px;
  letter-spacing: 0.16em;
}

.empty h2,
.cover h2,
.day h3,
.ledger h3,
.sources h3 {
  margin: 8px 0 0;
  font-family: "Noto Serif CJK SC", "Noto Serif CJK JP", serif;
  font-weight: 600;
}

.empty h2 {
  color: #12262c;
  font-size: 40px;
  line-height: 1.25;
}

.sample {
  display: block;
  width: 100%;
  margin-top: 22px;
  padding: 16px 18px;
  color: #3d342c;
  text-align: left;
  background: rgba(251, 248, 243, 0.72);
  border: 1px dashed rgba(18, 38, 44, 0.22);
  border-radius: 16px;
  cursor: pointer;
  line-height: 1.7;
}

.sample:hover {
  border-color: #1c4c57;
}

.line {
  max-width: 760px;
  margin: 0 auto 16px;
}

.line.user {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
}

.who {
  margin-bottom: 4px;
  color: #8a8176;
  font-size: 12px;
  letter-spacing: 0.12em;
}

.bubble {
  white-space: pre-wrap;
  word-break: break-word;
}

.bubble.user {
  max-width: min(640px, 86%);
  padding: 12px 16px;
  color: #f7f3ea;
  background: #1c4c57;
  border-radius: 18px 18px 6px 18px;
  line-height: 1.65;
}

.bubble.assistant {
  max-width: 100%;
  color: #1c2422;
  font-family: "Noto Serif CJK SC", "Noto Serif CJK JP", serif;
  font-size: 16.5px;
  line-height: 1.8;
}

.bubble.error {
  max-width: min(640px, 100%);
  padding: 12px 14px;
  color: #8d3328;
  background: #f8ebe6;
  border-radius: 12px;
}

.card {
  background: transparent;
}

.card :deep(.ant-collapse-item) {
  overflow: hidden;
  background: #fbf8f3;
  border: 1px solid rgba(18, 38, 44, 0.08);
  border-radius: 16px;
  box-shadow: 0 10px 28px rgba(18, 38, 44, 0.04);
}

.card :deep(.ant-collapse-header) {
  align-items: center !important;
  padding: 12px 14px !important;
}

.card :deep(.ant-collapse-content) {
  background: transparent;
  border-top: 1px solid rgba(18, 38, 44, 0.08);
}

.card :deep(.ant-collapse-content-box) {
  padding: 12px 16px 14px;
}

.tool-head {
  gap: 10px;
  width: 100%;
  min-width: 0;
}

.tool-kicker {
  flex: none;
  color: #1c4c57;
  letter-spacing: 0.12em;
}

.tool-arg {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  font-weight: 400;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pill {
  flex: none;
  padding: 2px 8px;
  font-size: 12px;
  font-weight: 500;
  border-radius: 999px;
}

.pill.ok {
  color: #24543d;
  background: #e5f0e8;
}

.pill.bad {
  color: #8d3328;
  background: #f8e6e2;
}

.pill.wait {
  color: #6e655b;
  background: #f0e7d8;
}

.facts {
  margin: 0;
  padding: 0;
  list-style: none;
}

.facts li,
.hit,
.ledger-row {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 8px 0;
  border-top: 1px solid rgba(18, 38, 44, 0.08);
}

.facts li:first-child,
.hit:first-child {
  border-top: 0;
  padding-top: 0;
}

.facts strong,
.hit a,
.hit-title,
.stop-name {
  color: #12262c;
  font-weight: 600;
}

.facts span,
.snippet,
.quiet,
.leg,
.unknown {
  color: #5e564c;
  font-size: 13px;
  line-height: 1.6;
}

.hit {
  display: block;
}

.hit a {
  color: #1c4c57;
  text-decoration: none;
}

.snippet {
  margin-top: 4px;
}

.route-ends {
  gap: 10px;
  color: #12262c;
  font-family: "Noto Serif CJK SC", "Noto Serif CJK JP", serif;
  font-size: 18px;
}

.route-ends em {
  color: #c6a15b;
  font-style: normal;
}

.route p,
.money-total {
  margin: 8px 0 0;
}

.money-total {
  color: #12262c;
  font-family: "Noto Serif CJK SC", "Noto Serif CJK JP", serif;
  font-size: 32px;
  line-height: 1;
}

.money-total small,
.cover-sum em {
  margin-left: 4px;
  font-family: "Noto Sans CJK SC", sans-serif;
  font-size: 14px;
  font-style: normal;
  font-weight: 500;
}

.fail {
  margin: 0;
  color: #8d3328;
}

.preview {
  max-height: 180px;
  margin: 0 0 12px;
  overflow: auto;
  color: #3d342c;
  font-size: 12.5px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.composer {
  margin: 0 8vw 20px;
  padding: 10px 12px 12px;
  background: #fbf8f3;
  border: 1px solid rgba(18, 38, 44, 0.08);
  border-radius: 18px;
  box-shadow: 0 16px 40px rgba(18, 38, 44, 0.08);
}

.composer :deep(.ant-input) {
  padding: 6px 8px;
  font-size: 15px;
  background: transparent;
  border: none;
  box-shadow: none;
  resize: none;
}

.composer :deep(.ant-input:focus) {
  box-shadow: none;
}

.composer-bar {
  justify-content: space-between;
  gap: 12px;
  padding: 4px 8px 0;
  font-size: 12px;
}

.side :deep(.ant-layout-sider-children) {
  height: 100%;
}

.side-empty,
.book {
  padding: 28px 24px 36px;
}

.ticket {
  padding: 22px 18px;
  background:
    linear-gradient(#12262c, #12262c) padding-box,
    linear-gradient(135deg, rgba(198, 161, 91, 0.8), rgba(244, 241, 234, 0.12)) border-box;
  border: 1px solid transparent;
  border-radius: 18px;
}

.ticket span,
.cover-kicker {
  color: #d7b56a;
  font-size: 12px;
  letter-spacing: 0.16em;
}

.ticket strong {
  display: block;
  margin-top: 10px;
  font-family: "Noto Serif CJK SC", "Noto Serif CJK JP", serif;
  font-size: 28px;
  font-weight: 600;
}

.ticket p,
.side-note,
.cover-dates,
.weather,
.leg,
.unknown {
  color: rgba(244, 241, 234, 0.68);
}

.ticket p {
  margin: 10px 0 0;
  line-height: 1.6;
}

.cover h2 {
  color: #f7f3ea;
  font-size: 32px;
  line-height: 1.3;
}

.tags {
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
}

.tags span,
.period {
  padding: 3px 8px;
  color: #e7d3a4;
  font-size: 12px;
  background: rgba(231, 211, 164, 0.1);
  border-radius: 999px;
}

.cover-dates {
  margin: 12px 0 0;
}

.cover-sum {
  gap: 8px;
  margin-top: 18px;
  align-items: baseline;
}

.cover-sum span {
  color: rgba(244, 241, 234, 0.62);
}

.cover-sum strong {
  color: #f7f3ea;
  font-family: "Noto Serif CJK SC", "Noto Serif CJK JP", serif;
  font-size: 36px;
  font-weight: 600;
  line-height: 1;
}

.download {
  margin-top: 16px;
  color: #12262c !important;
  background: #d7b56a !important;
  border-color: #d7b56a !important;
}

.day {
  display: grid;
  grid-template-columns: 36px 1fr;
  gap: 12px;
  margin-top: 26px;
}

.day-index {
  color: #d7b56a;
  font-family: "Noto Serif CJK SC", "Noto Serif CJK JP", serif;
  font-size: 18px;
}

.day h3,
.ledger h3,
.sources h3 {
  color: #f7f3ea;
  font-size: 18px;
}

.weather,
.leg,
.stop-cat {
  margin: 4px 0 0;
  font-size: 13px;
}

.stop {
  display: flex;
  gap: 10px;
  align-items: baseline;
  margin-top: 12px;
}

.stop-name {
  color: #f7f3ea;
}

.period {
  flex: none;
}

.leg {
  margin: 8px 0 0 8px;
  padding-left: 10px;
  border-left: 1px dashed rgba(215, 181, 106, 0.45);
}

.ledger,
.sources {
  margin-top: 28px;
  padding-top: 8px;
  border-top: 1px solid rgba(244, 241, 234, 0.1);
}

.ledger-row {
  color: rgba(244, 241, 234, 0.86);
  border-color: rgba(244, 241, 234, 0.1);
}

.sources a {
  display: inline-block;
  margin-top: 8px;
  color: #e7d3a4;
}

.side-note {
  margin: 28px 0 0;
  font-size: 12px;
  line-height: 1.6;
}

.modal-hint {
  margin: 0 0 14px;
  line-height: 1.6;
}

.now {
  display: grid;
  gap: 8px;
  margin: 0 0 16px;
}

.now div {
  display: grid;
  grid-template-columns: 72px 1fr;
  gap: 8px;
}

.now dt {
  color: #8a8176;
}

.now dd {
  margin: 0;
  overflow-wrap: anywhere;
}

.models {
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}

.chip {
  padding: 4px 10px;
  color: #1c4c57;
  background: #f3efe6;
  border: 1px solid #e4d8c8;
  border-radius: 999px;
  cursor: pointer;
}

.modal-actions {
  flex-wrap: wrap;
  gap: 8px;
}

@media (max-width: 960px) {
  .body {
    flex-direction: column;
  }

  .messages,
  .composer {
    padding-right: 16px;
    padding-left: 16px;
  }

  .composer {
    margin-right: 16px;
    margin-left: 16px;
  }

  .side {
    width: 100% !important;
    max-width: none !important;
    flex: none !important;
    height: 46vh;
  }

  .empty h2 {
    font-size: 30px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .status.busy i {
    animation: none;
  }
}
</style>
