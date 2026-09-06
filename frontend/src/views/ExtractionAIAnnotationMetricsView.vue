<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import { getApiErrorMessage } from '../api/client'
import {
  type AIAnnotationMetrics,
  type AIAnnotationMetricsRecord,
  fetchAIAnnotationMetrics,
} from '../api/aiAnnotations'
import AppLayout from '../layouts/AppLayout.vue'

type RecordFilter = 'all' | 'submitted' | 'pending' | 'failed'

const days = ref(30)
const loading = ref(false)
const errorMessage = ref('')
const metrics = ref<AIAnnotationMetrics | null>(null)
const recordFilter = ref<RecordFilter>('all')
const searchQuery = ref('')
const selectedErrorCode = ref('')
const expandedSessionId = ref<number | null>(null)

const summary = computed(() => metrics.value?.summary ?? null)
const maxDailyTokens = computed(() => Math.max(
  1,
  ...(metrics.value?.daily?.map((item) => item.total_tokens) ?? [1]),
))
const maxDailyErrors = computed(() => Math.max(
  1,
  ...(metrics.value?.daily?.map((item) => item.first_round_error_count) ?? [1]),
))
const maxValidationErrors = computed(() => Math.max(
  1,
  ...(metrics.value?.validation_errors?.map((item) => item.count) ?? [1]),
))
const maxFinalDifferences = computed(() => Math.max(
  1,
  ...(metrics.value?.final_differences?.map((item) => item.count) ?? [1]),
))

const visibleRecords = computed(() => {
  const keyword = searchQuery.value.trim().toLocaleLowerCase()
  return (metrics.value?.records ?? []).filter((record) => {
    if (recordFilter.value === 'submitted' && !record.submitted_at) return false
    if (
      recordFilter.value === 'pending'
      && (record.submitted_at || record.first_round_validation_status === 'failed')
    ) return false
    if (
      recordFilter.value === 'failed'
      && record.first_round_validation_status !== 'failed'
      && record.status !== 'failed'
    ) return false
    if (
      selectedErrorCode.value
      && !record.first_round_errors.some((issue) => issue.code === selectedErrorCode.value)
    ) return false
    if (!keyword) return true
    return [
      record.passage_title,
      record.requested_by_email,
      record.provider,
      record.model,
      String(record.session_id),
      String(record.task_id),
    ].some((value) => value.toLocaleLowerCase().includes(keyword))
  })
})

const cacheHitRate = computed(() => {
  if (!summary.value?.total_prompt_tokens) return 0
  return summary.value.total_cache_hit_tokens / summary.value.total_prompt_tokens
})

async function loadMetrics() {
  loading.value = true
  errorMessage.value = ''
  try {
    metrics.value = await fetchAIAnnotationMetrics(days.value)
  } catch (error) {
    errorMessage.value = getApiErrorMessage(error, 'AI 标注统计加载失败。')
  } finally {
    loading.value = false
  }
}

function formatNumber(value: number | null | undefined) {
  return Math.max(Number(value ?? 0), 0).toLocaleString('zh-CN')
}

function formatPercent(value: number | null | undefined) {
  return `${(Math.max(Number(value ?? 0), 0) * 100).toFixed(1)}%`
}

function formatDuration(value: number | null | undefined) {
  if (value === null || value === undefined) return '—'
  if (value < 1000) return `${value} ms`
  if (value < 60_000) return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)} 秒`
  if (value < 3_600_000) return `${(value / 60_000).toFixed(1)} 分钟`
  return `${(value / 3_600_000).toFixed(1)} 小时`
}

function formatDate(value: string | null | undefined) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date)
}

function shortDate(value: string) {
  const [, month, day] = value.split('-')
  return `${month}/${day}`
}

function barHeight(value: number, maximum: number, minimum = 4) {
  if (value <= 0) return `${minimum}px`
  return `${Math.max(Math.round((value / maximum) * 112), minimum)}px`
}

function barWidth(value: number, maximum: number) {
  if (value <= 0) return '0%'
  return `${Math.max((value / maximum) * 100, 2)}%`
}

function firstRoundLabel(status: string) {
  if (status === 'valid') return '首轮通过'
  if (status === 'invalid_rules') return '规则错误'
  if (status === 'invalid_format') return '格式错误'
  if (status === 'failed') return '调用失败'
  return '进行中'
}

function sessionState(record: AIAnnotationMetricsRecord) {
  if (record.submitted_at) return '已提交'
  if (record.status === 'failed' || record.first_round_validation_status === 'failed') return '失败'
  if (record.status === 'queued') return '排队中'
  if (record.status === 'running') return '执行中'
  return '待提交'
}

function toggleRecord(sessionId: number) {
  expandedSessionId.value = expandedSessionId.value === sessionId ? null : sessionId
}

function selectError(code: string) {
  selectedErrorCode.value = selectedErrorCode.value === code ? '' : code
  expandedSessionId.value = null
}

function clearFilters() {
  recordFilter.value = 'all'
  searchQuery.value = ''
  selectedErrorCode.value = ''
}

watch(days, () => {
  selectedErrorCode.value = ''
  expandedSessionId.value = null
  void loadMetrics()
})

onMounted(() => {
  void loadMetrics()
})
</script>

<template>
  <AppLayout variant="workspace">
    <div class="metrics-page">
      <header class="metrics-topbar">
        <div class="metrics-title">
          <span class="eyebrow">A · OBSERVATORY</span>
          <div>
            <h2>AI 标注统计</h2>
            <p>一次生成请求为一个实验会话，汇总其修复轮次直到最终人工提交。</p>
          </div>
        </div>
        <nav class="mode-switch" aria-label="抽取标注模式">
          <RouterLink to="/annotations/extraction">独立盲标</RouterLink>
          <RouterLink to="/annotations/extraction/ai">AI 标注</RouterLink>
          <span>统计</span>
          <RouterLink to="/annotations/extraction/adjudication">复核裁定</RouterLink>
        </nav>
        <div class="topbar-actions">
          <label>
            <span>统计范围</span>
            <select v-model.number="days">
              <option :value="7">近 7 天</option>
              <option :value="30">近 30 天</option>
              <option :value="90">近 90 天</option>
              <option :value="365">近 1 年</option>
              <option :value="0">全部记录</option>
            </select>
          </label>
          <button type="button" :disabled="loading" @click="loadMetrics">
            {{ loading ? '读取中…' : '刷新数据' }}
          </button>
        </div>
      </header>

      <main class="metrics-scroll">
        <p v-if="errorMessage" class="error-banner">{{ errorMessage }}</p>

        <section class="metric-strip" aria-label="核心指标">
          <article>
            <span>生成会话</span>
            <strong>{{ formatNumber(summary?.session_count) }}</strong>
            <small>{{ formatNumber(summary?.submitted_count) }} 次已提交</small>
          </article>
          <article>
            <span>首轮通过率</span>
            <strong>{{ formatPercent(summary?.first_pass_valid_rate) }}</strong>
            <small>{{ formatNumber(summary?.total_first_round_errors) }} 条首轮错误</small>
          </article>
          <article>
            <span>点击 → 提交</span>
            <strong>{{ formatDuration(summary?.average_end_to_end_ms) }}</strong>
            <small>仅统计已正式提交会话</small>
          </article>
          <article>
            <span>首轮执行耗时</span>
            <strong>{{ formatDuration(summary?.average_first_round_ms) }}</strong>
            <small>含全部语义分片</small>
          </article>
          <article>
            <span>累计 Token</span>
            <strong>{{ formatNumber(summary?.total_tokens) }}</strong>
            <small>输入 {{ formatNumber(summary?.total_prompt_tokens) }} · 输出 {{ formatNumber(summary?.total_completion_tokens) }}</small>
          </article>
          <article>
            <span>模型调用</span>
            <strong>{{ formatNumber(summary?.total_llm_call_count) }}</strong>
            <small>{{ formatNumber(summary?.total_repair_rounds) }} 轮修复 · 缓存 {{ formatPercent(cacheHitRate) }}</small>
          </article>
        </section>

        <section class="analysis-grid">
          <div class="trend-panel">
            <header class="section-heading">
              <div>
                <span>DAILY TRACE</span>
                <h3>调用量与首轮错误</h3>
              </div>
              <div class="legend">
                <span><i class="legend__token" />Token</span>
                <span><i class="legend__error" />错误</span>
              </div>
            </header>
            <div v-if="metrics?.daily?.length" class="daily-chart">
              <div v-for="item in metrics.daily" :key="item.date" class="daily-column">
                <div class="daily-bars">
                  <i
                    class="daily-bar daily-bar--token"
                    :style="{ height: barHeight(item.total_tokens, maxDailyTokens) }"
                    :title="`${formatNumber(item.total_tokens)} Token`"
                  />
                  <i
                    class="daily-bar daily-bar--error"
                    :style="{ height: barHeight(item.first_round_error_count, maxDailyErrors) }"
                    :title="`${item.first_round_error_count} 条首轮错误`"
                  />
                </div>
                <strong>{{ item.session_count }}</strong>
                <span>{{ shortDate(item.date) }}</span>
              </div>
            </div>
            <div v-else class="empty-state">
              <strong>暂无趋势数据</strong>
              <span>完成第一条 AI 标注后，这里会按天显示调用记录。</span>
            </div>
          </div>

          <div class="error-panel">
            <header class="section-heading">
              <div>
                <span>FIRST ROUND</span>
                <h3>首轮错误类型</h3>
              </div>
              <small>点击类型筛选下方记录</small>
            </header>
            <div v-if="metrics?.validation_errors?.length" class="rank-list">
              <button
                v-for="item in metrics.validation_errors.slice(0, 8)"
                :key="item.code"
                type="button"
                :class="{ active: selectedErrorCode === item.code }"
                @click="selectError(item.code)"
              >
                <span class="rank-list__label">
                  <code>{{ item.code }}</code>
                  <small>{{ item.session_count }} 个会话</small>
                </span>
                <i><b :style="{ width: barWidth(item.count, maxValidationErrors) }" /></i>
                <strong>{{ item.count }}</strong>
              </button>
            </div>
            <div v-else class="empty-state empty-state--compact">
              <strong>当前范围没有首轮错误</strong>
              <span>首轮通过的会话不会进入错误排行。</span>
            </div>
            <div class="difference-block">
              <div class="difference-block__title">
                <span>首轮 → 最终提交</span>
                <strong>{{ formatNumber(summary?.total_final_differences) }} 处修改</strong>
              </div>
              <div v-if="metrics?.final_differences?.length" class="difference-bars">
                <div v-for="item in metrics.final_differences" :key="item.category">
                  <span>{{ item.label }}</span>
                  <i><b :style="{ width: barWidth(item.count, maxFinalDifferences) }" /></i>
                  <strong>{{ item.count }}</strong>
                </div>
              </div>
              <p v-else>已提交会话尚无人工修改差异。</p>
            </div>
          </div>
        </section>

        <section class="records-panel">
          <header class="records-heading">
            <div>
              <span>SESSION LOG</span>
              <h3>逐次标注记录</h3>
              <p>首轮生成、后续修复与最终提交按来源任务串成同一条记录。</p>
            </div>
            <div class="record-tools">
              <div class="filter-tabs" aria-label="提交状态筛选">
                <button
                  v-for="item in ([
                    ['all', '全部'],
                    ['submitted', '已提交'],
                    ['pending', '待提交'],
                    ['failed', '失败'],
                  ] as [RecordFilter, string][] )"
                  :key="item[0]"
                  type="button"
                  :class="{ active: recordFilter === item[0] }"
                  @click="recordFilter = item[0]"
                >{{ item[1] }}</button>
              </div>
              <label class="record-search">
                <span>搜索</span>
                <input v-model="searchQuery" type="search" placeholder="篇名 / 用户 / 模型 / ID" />
              </label>
              <button
                v-if="selectedErrorCode || searchQuery || recordFilter !== 'all'"
                class="clear-button"
                type="button"
                @click="clearFilters"
              >清除筛选</button>
            </div>
          </header>

          <div v-if="loading && !metrics" class="table-state">正在读取实验记录…</div>
          <div v-else-if="!visibleRecords.length" class="table-state">
            {{ metrics?.records?.length ? '没有符合当前条件的记录。' : '当前范围暂无 AI 标注记录。' }}
          </div>
          <div v-else class="record-table-wrap">
            <table class="record-table">
              <thead>
                <tr>
                  <th>会话 / 篇目</th>
                  <th>标注人</th>
                  <th>首轮质量</th>
                  <th>点击 → 提交</th>
                  <th>Token</th>
                  <th>修复</th>
                  <th>状态</th>
                  <th aria-label="展开" />
                </tr>
              </thead>
              <tbody>
                <template v-for="record in visibleRecords" :key="record.session_id">
                  <tr
                    class="record-row"
                    :class="{ expanded: expandedSessionId === record.session_id }"
                    tabindex="0"
                    @click="toggleRecord(record.session_id)"
                    @keydown.enter="toggleRecord(record.session_id)"
                  >
                    <td>
                      <strong>{{ record.passage_title }}</strong>
                      <span>#{{ record.session_id }} · 任务 {{ record.task_id }} · {{ formatDate(record.client_started_at) }}</span>
                    </td>
                    <td>
                      <strong>{{ record.requested_by_email }}</strong>
                      <span>{{ record.provider || '默认 Provider' }} · {{ record.model || '默认模型' }}</span>
                    </td>
                    <td>
                      <b class="quality" :data-status="record.first_round_validation_status">
                        {{ firstRoundLabel(record.first_round_validation_status) }}
                      </b>
                      <span>{{ record.first_round_errors.length }} 错误 · {{ record.final_difference_count }} 修改</span>
                    </td>
                    <td>
                      <strong>{{ formatDuration(record.end_to_end_ms) }}</strong>
                      <span>首轮 {{ formatDuration(record.first_round_elapsed_ms) }}</span>
                    </td>
                    <td>
                      <strong>{{ formatNumber(record.token_usage.total_tokens) }}</strong>
                      <span>{{ formatNumber(record.token_usage.prompt_tokens) }} / {{ formatNumber(record.token_usage.completion_tokens) }}</span>
                    </td>
                    <td>
                      <strong>{{ record.repair_rounds }}</strong>
                      <span>{{ record.token_usage.llm_call_count }} 次调用</span>
                    </td>
                    <td>
                      <b class="session-state" :data-state="sessionState(record)">{{ sessionState(record) }}</b>
                    </td>
                    <td><i class="chevron">⌄</i></td>
                  </tr>
                  <tr v-if="expandedSessionId === record.session_id" class="record-detail-row">
                    <td colspan="8">
                      <div class="record-detail">
                        <div class="lifecycle">
                          <div>
                            <span>点击生成</span>
                            <strong>{{ formatDate(record.client_started_at) }}</strong>
                          </div>
                          <i />
                          <div>
                            <span>开始执行</span>
                            <strong>{{ formatDate(record.started_at) }}</strong>
                            <small>排队 {{ formatDuration(record.queue_wait_ms) }}</small>
                          </div>
                          <i />
                          <div>
                            <span>首轮完成</span>
                            <strong>{{ formatDate(record.first_round_finished_at) }}</strong>
                            <small>{{ firstRoundLabel(record.first_round_validation_status) }}</small>
                          </div>
                          <i />
                          <div>
                            <span>正式提交</span>
                            <strong>{{ formatDate(record.submitted_at) }}</strong>
                            <small v-if="record.submitted_at">总计 {{ formatDuration(record.end_to_end_ms) }}</small>
                            <small v-else>尚未提交盲标</small>
                          </div>
                        </div>
                        <div class="detail-columns">
                          <section>
                            <span>TOKEN BREAKDOWN</span>
                            <dl>
                              <div><dt>输入 Token</dt><dd>{{ formatNumber(record.token_usage.prompt_tokens) }}</dd></div>
                              <div><dt>输出 Token</dt><dd>{{ formatNumber(record.token_usage.completion_tokens) }}</dd></div>
                              <div><dt>缓存命中</dt><dd>{{ formatNumber(record.token_usage.prompt_cache_hit_tokens) }}</dd></div>
                              <div><dt>缓存未命中</dt><dd>{{ formatNumber(record.token_usage.prompt_cache_miss_tokens) }}</dd></div>
                            </dl>
                          </section>
                          <section>
                            <span>FIRST-ROUND ISSUES</span>
                            <ul v-if="record.first_round_errors.length">
                              <li v-for="issue in record.first_round_errors" :key="`${issue.path}:${issue.code}`">
                                <code>{{ issue.code }}</code>
                                <strong>{{ issue.path }}</strong>
                                <p>{{ issue.message }}</p>
                              </li>
                            </ul>
                            <p v-else class="detail-empty">首轮没有格式或规则错误。</p>
                          </section>
                          <section>
                            <span>FINAL CORRECTIONS</span>
                            <dl v-if="Object.keys(record.final_difference_categories).length">
                              <div v-for="(count, category) in record.final_difference_categories" :key="category">
                                <dt>{{ category }}</dt>
                                <dd>{{ count }}</dd>
                              </div>
                            </dl>
                            <p v-else class="detail-empty">
                              {{ record.submitted_at ? '首轮结果与最终提交无结构化差异。' : '正式提交后生成差异统计。' }}
                            </p>
                          </section>
                        </div>
                      </div>
                    </td>
                  </tr>
                </template>
              </tbody>
            </table>
          </div>
        </section>
      </main>
    </div>
  </AppLayout>
</template>

<style scoped>
.metrics-page {
  --ink: #293b62;
  --muted: #72809e;
  --line: #e5eaf3;
  --blue: #2f6fed;
  --red: #b95555;
  height: 100%;
  min-height: 0;
  color: var(--ink);
  background: #f7f9fc;
}

.metrics-topbar {
  min-height: 86px;
  display: grid;
  grid-template-columns: minmax(300px, 1fr) auto auto;
  align-items: center;
  gap: 24px;
  padding: 14px 26px;
  border-bottom: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.96);
}

.metrics-title { display: flex; align-items: center; gap: 14px; }
.eyebrow { color: var(--blue); font: 700 10px/1.2 ui-monospace, monospace; letter-spacing: .14em; }
.metrics-title h2 { margin: 0; font-size: 21px; letter-spacing: -.02em; }
.metrics-title p { margin: 4px 0 0; color: var(--muted); font-size: 11px; }

.mode-switch { display: flex; align-items: center; gap: 4px; padding: 3px; border: 1px solid var(--line); border-radius: 9px; background: #f8faff; }
.mode-switch a, .mode-switch span { padding: 7px 10px; border-radius: 6px; color: #687794; font-size: 10px; }
.mode-switch a:hover { color: var(--blue); }
.mode-switch span { color: #fff; background: var(--blue); font-weight: 700; }

.topbar-actions { display: flex; align-items: flex-end; gap: 8px; }
.topbar-actions label { display: grid; gap: 4px; color: var(--muted); font-size: 9px; }
.topbar-actions select, .topbar-actions button, .record-search input {
  height: 32px;
  border: 1px solid #dce3ef;
  border-radius: 7px;
  background: #fff;
  color: var(--ink);
  font-size: 10px;
}
.topbar-actions select { min-width: 112px; padding: 0 26px 0 10px; }
.topbar-actions button { padding: 0 13px; cursor: pointer; transition: border-color .18s ease, color .18s ease, transform .18s ease; }
.topbar-actions button:hover:not(:disabled) { border-color: var(--blue); color: var(--blue); transform: translateY(-1px); }

.metrics-scroll { height: calc(100vh - 86px); overflow: auto; padding: 24px 28px 38px; animation: page-in .42s ease both; }
.error-banner { margin: 0 0 16px; padding: 10px 14px; border-left: 3px solid var(--red); color: #9d4242; background: #fff5f5; font-size: 11px; }

.metric-strip {
  display: grid;
  grid-template-columns: repeat(6, minmax(125px, 1fr));
  border-block: 1px solid var(--line);
  background: #fff;
}
.metric-strip article { min-width: 0; padding: 18px 18px 16px; border-right: 1px solid var(--line); }
.metric-strip article:last-child { border-right: 0; }
.metric-strip span { display: block; color: var(--muted); font-size: 9px; letter-spacing: .05em; }
.metric-strip strong { display: block; margin-top: 8px; color: var(--ink); font-size: clamp(19px, 2vw, 27px); line-height: 1; letter-spacing: -.04em; white-space: nowrap; }
.metric-strip small { display: block; margin-top: 8px; overflow: hidden; color: #8a96ad; font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }

.analysis-grid { display: grid; grid-template-columns: minmax(0, 1.45fr) minmax(330px, .75fr); gap: 1px; margin-top: 22px; background: var(--line); border-block: 1px solid var(--line); }
.trend-panel, .error-panel { min-width: 0; padding: 20px 22px; background: #fff; }
.section-heading, .records-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; }
.section-heading > div > span, .records-heading > div > span, .detail-columns section > span { color: var(--blue); font: 700 9px/1.2 ui-monospace, monospace; letter-spacing: .14em; }
.section-heading h3, .records-heading h3 { margin: 5px 0 0; font-size: 15px; }
.section-heading > small { color: var(--muted); font-size: 9px; }
.legend { display: flex; gap: 12px; color: var(--muted); font-size: 9px; }
.legend span { display: flex; align-items: center; gap: 5px; }
.legend i { width: 7px; height: 7px; border-radius: 2px; }
.legend__token { background: var(--blue); }
.legend__error { background: var(--red); }

.daily-chart { height: 164px; display: flex; align-items: end; gap: clamp(7px, 1.3vw, 18px); margin-top: 20px; padding: 0 4px; overflow-x: auto; }
.daily-column { min-width: 28px; flex: 1; display: grid; justify-items: center; gap: 3px; color: var(--muted); font-size: 8px; }
.daily-bars { height: 116px; display: flex; align-items: end; gap: 3px; }
.daily-bar { width: 6px; display: block; border-radius: 2px 2px 0 0; transform-origin: bottom; animation: bar-rise .55s cubic-bezier(.22,.8,.28,1) both; transition: filter .18s ease, height .35s ease; }
.daily-bar:hover { filter: brightness(.82); }
.daily-bar--token { background: #5d86e8; }
.daily-bar--error { background: #cb6a6a; }
.daily-column strong { color: var(--ink); font-size: 9px; }

.rank-list { display: grid; gap: 7px; margin-top: 17px; }
.rank-list button { display: grid; grid-template-columns: minmax(130px, 1.4fr) minmax(70px, 1fr) 26px; align-items: center; gap: 9px; padding: 5px 7px; border: 0; border-radius: 5px; background: transparent; text-align: left; cursor: pointer; transition: background .18s ease; }
.rank-list button:hover, .rank-list button.active { background: #f2f6ff; }
.rank-list__label { min-width: 0; display: flex; align-items: baseline; gap: 7px; }
.rank-list code { overflow: hidden; color: #4f5e7a; font-size: 9px; text-overflow: ellipsis; }
.rank-list small { color: #99a3b7; font-size: 8px; white-space: nowrap; }
.rank-list button > i, .difference-bars i { height: 4px; overflow: hidden; border-radius: 9px; background: #eef1f6; }
.rank-list button > i b, .difference-bars i b { height: 100%; display: block; border-radius: inherit; background: var(--red); transition: width .45s cubic-bezier(.22,.8,.28,1); }
.rank-list button > strong { font-size: 10px; text-align: right; }

.difference-block { margin-top: 17px; padding-top: 14px; border-top: 1px solid var(--line); }
.difference-block__title { display: flex; justify-content: space-between; color: var(--muted); font-size: 9px; }
.difference-block__title strong { color: var(--ink); }
.difference-bars { display: grid; gap: 6px; margin-top: 10px; }
.difference-bars > div { display: grid; grid-template-columns: 86px 1fr 24px; align-items: center; gap: 8px; font-size: 9px; }
.difference-bars i b { background: #8d9bb6; }
.difference-bars strong { text-align: right; }
.difference-block p { margin: 10px 0 0; color: #9aa4b7; font-size: 9px; }

.empty-state { min-height: 145px; display: grid; place-content: center; justify-items: center; gap: 5px; color: var(--muted); font-size: 9px; }
.empty-state strong { color: var(--ink); font-size: 11px; }
.empty-state--compact { min-height: 78px; }

.records-panel { margin-top: 22px; border-block: 1px solid var(--line); background: #fff; }
.records-heading { padding: 18px 20px; border-bottom: 1px solid var(--line); }
.records-heading p { margin: 5px 0 0; color: var(--muted); font-size: 9px; }
.record-tools { display: flex; align-items: end; gap: 8px; }
.filter-tabs { display: flex; padding: 2px; border: 1px solid var(--line); border-radius: 7px; background: #f7f9fc; }
.filter-tabs button { height: 26px; padding: 0 9px; border: 0; border-radius: 5px; color: var(--muted); background: transparent; font-size: 9px; cursor: pointer; }
.filter-tabs button.active { color: var(--blue); background: #fff; box-shadow: 0 1px 4px rgba(45, 68, 109, .1); }
.record-search { display: grid; gap: 4px; color: var(--muted); font-size: 8px; }
.record-search input { width: 178px; padding: 0 10px; }
.clear-button { height: 32px; border: 0; color: var(--blue); background: transparent; font-size: 9px; cursor: pointer; }

.record-table-wrap { overflow-x: auto; }
.record-table { width: 100%; min-width: 1000px; border-collapse: collapse; table-layout: fixed; }
.record-table th { padding: 9px 12px; color: #929db2; background: #fafbfd; font-size: 8px; font-weight: 600; letter-spacing: .05em; text-align: left; }
.record-table th:nth-child(1) { width: 21%; }
.record-table th:nth-child(2) { width: 17%; }
.record-table th:nth-child(3) { width: 13%; }
.record-table th:nth-child(4) { width: 13%; }
.record-table th:nth-child(5) { width: 12%; }
.record-table th:nth-child(6) { width: 9%; }
.record-table th:nth-child(7) { width: 10%; }
.record-table th:nth-child(8) { width: 4%; }
.record-row { border-top: 1px solid #edf0f5; cursor: pointer; outline: none; transition: background .16s ease; }
.record-row:hover, .record-row:focus, .record-row.expanded { background: #f7faff; }
.record-row td { height: 59px; padding: 9px 12px; vertical-align: middle; }
.record-row td > strong, .record-row td > span { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.record-row td > strong { color: #344563; font-size: 10px; }
.record-row td > span { margin-top: 4px; color: #8b97ad; font-size: 8px; }
.quality, .session-state { display: inline-block; padding: 3px 6px; border-radius: 4px; color: #3f7e62; background: #edf8f3; font-size: 8px; }
.quality[data-status='invalid_rules'], .quality[data-status='invalid_format'], .quality[data-status='failed'] { color: #a24c4c; background: #fff0f0; }
.session-state[data-state='待提交'], .session-state[data-state='排队中'], .session-state[data-state='执行中'] { color: #966d25; background: #fff6e3; }
.session-state[data-state='失败'] { color: #a24c4c; background: #fff0f0; }
.chevron { display: block; color: #8895ac; font-style: normal; font-size: 15px; transform: rotate(0); transition: transform .2s ease; }
.record-row.expanded .chevron { transform: rotate(180deg); }

.record-detail-row td { padding: 0; border-top: 1px solid #e8edf6; }
.record-detail { padding: 18px 22px 22px; background: #f8faff; animation: detail-in .24s ease both; }
.lifecycle { display: grid; grid-template-columns: minmax(115px, 1fr) 28px minmax(115px, 1fr) 28px minmax(115px, 1fr) 28px minmax(115px, 1fr); align-items: center; }
.lifecycle > div { display: grid; gap: 3px; }
.lifecycle span { color: var(--muted); font-size: 8px; }
.lifecycle strong { font-size: 10px; }
.lifecycle small { color: #8490a6; font-size: 8px; }
.lifecycle > i { height: 1px; margin: 0 7px; background: #cdd7e8; }
.detail-columns { display: grid; grid-template-columns: .7fr 1.35fr .8fr; gap: 22px; margin-top: 19px; padding-top: 16px; border-top: 1px solid #dfe6f1; }
.detail-columns section { min-width: 0; }
.detail-columns dl { display: grid; gap: 5px; margin: 10px 0 0; }
.detail-columns dl div { display: flex; justify-content: space-between; gap: 12px; font-size: 9px; }
.detail-columns dt { color: var(--muted); }
.detail-columns dd { margin: 0; font-weight: 700; }
.detail-columns ul { max-height: 150px; overflow: auto; display: grid; gap: 6px; margin: 10px 0 0; padding: 0; list-style: none; }
.detail-columns li { display: grid; grid-template-columns: auto 1fr; gap: 3px 8px; padding-bottom: 6px; border-bottom: 1px solid #e3e9f3; font-size: 8px; }
.detail-columns li code { color: var(--red); }
.detail-columns li strong { overflow: hidden; color: #566680; text-overflow: ellipsis; white-space: nowrap; }
.detail-columns li p { grid-column: 1 / -1; margin: 0; color: var(--muted); }
.detail-empty { margin: 10px 0 0; color: var(--muted); font-size: 9px; }
.table-state { min-height: 160px; display: grid; place-content: center; color: var(--muted); font-size: 10px; }

@keyframes page-in { from { opacity: 0; transform: translateY(7px); } to { opacity: 1; transform: translateY(0); } }
@keyframes bar-rise { from { transform: scaleY(0); opacity: .3; } to { transform: scaleY(1); opacity: 1; } }
@keyframes detail-in { from { opacity: 0; transform: translateY(-5px); } to { opacity: 1; transform: translateY(0); } }

@media (max-width: 1280px) {
  .metrics-topbar { grid-template-columns: 1fr auto; }
  .mode-switch { display: none; }
  .metric-strip { grid-template-columns: repeat(3, 1fr); }
  .metric-strip article:nth-child(3) { border-right: 0; }
  .metric-strip article:nth-child(-n+3) { border-bottom: 1px solid var(--line); }
  .analysis-grid { grid-template-columns: 1fr; }
}

@media (max-width: 820px) {
  .metrics-topbar { grid-template-columns: 1fr; gap: 10px; padding: 14px 18px; }
  .topbar-actions { justify-content: flex-start; }
  .metrics-scroll { height: auto; padding: 18px 14px 30px; }
  .metric-strip { grid-template-columns: repeat(2, 1fr); }
  .metric-strip article { border-bottom: 1px solid var(--line); }
  .metric-strip article:nth-child(2n) { border-right: 0; }
  .records-heading { display: grid; }
  .record-tools { flex-wrap: wrap; }
  .lifecycle { grid-template-columns: 1fr; gap: 9px; }
  .lifecycle > i { width: 1px; height: 12px; margin: 0 0 0 3px; }
  .detail-columns { grid-template-columns: 1fr; }
}

@media (prefers-reduced-motion: reduce) {
  .metrics-scroll, .daily-bar, .record-detail { animation: none; }
  *, *::before, *::after { scroll-behavior: auto !important; transition-duration: .01ms !important; }
}
</style>
