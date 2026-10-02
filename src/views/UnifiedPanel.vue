<template>
  <section
    class="panel"
    :class="[`panel--${side}`, { 'is-open': isOpen, 'is-collapsed': collapsed }]"
  >
    <!-- ── 头部 ── -->
    <header class="panel__head">
      <div class="panel__title">
        <i class="fas" :class="side === 'left' ? 'fa-compass' : 'fa-sliders'" aria-hidden="true"></i>
        <span>{{ panelTitle }}</span>
      </div>
      <button
        class="btn btn--ghost btn--sm panel__collapse"
        :aria-expanded="!collapsed"
        :aria-label="collapsed ? '展开面板' : '收起面板'"
        @click="collapsed = !collapsed"
      >
        <i class="fas fa-chevron-up" :class="collapsed ? 'rot' : ''" aria-hidden="true"></i>
      </button>
    </header>

    <!-- ── 标签页 ── -->
    <div v-show="!collapsed" class="tablist" role="tablist" :aria-label="panelTitle">
      <button
        v-for="tab in tabs"
        :id="`${uid}-tab-${tab.id}`"
        :key="tab.id"
        class="tab"
        role="tab"
        type="button"
        :aria-selected="activeTab === tab.id"
        :aria-controls="`${uid}-panel-${tab.id}`"
        :tabindex="activeTab === tab.id ? 0 : -1"
        @click="activeTab = tab.id"
        @keydown="onTabKey($event, tab.id)"
      >
        <i class="fas" :class="tab.icon" aria-hidden="true"></i>
        <span>{{ tab.name }}</span>
        <span
          v-if="tab.id === 'fire' && ops.activeFires.length"
          class="tab__badge"
        >{{ ops.activeFires.length }}</span>
        <span
          v-if="tab.id === 'alerts' && activeAlerts.length"
          class="tab__badge"
        >{{ activeAlerts.length }}</span>
      </button>
    </div>

    <!-- ── 面板主体 ── -->
    <div v-show="!collapsed" class="tabpanel" role="tabpanel" :tabindex="0">

      <!-- ═══ 舰船信息 ═══ -->
      <template v-if="activeTab === 'ship'">
        <section class="card">
          <header class="card__head">
            <h2 class="card__title">舰船基础信息</h2>
          </header>
          <div class="card__body">
            <dl class="stat-grid">
              <div class="stat">
                <dt class="stat__label">舰名</dt>
                <dd class="stat__value">{{ ship.name }}</dd>
              </div>
              <div class="stat">
                <dt class="stat__label">舷号</dt>
                <dd class="stat__value t-mono">{{ ship.hullNumber }}</dd>
              </div>
              <div class="stat">
                <dt class="stat__label">舰型</dt>
                <dd class="stat__value">{{ ship.type }}</dd>
              </div>
              <div class="stat">
                <dt class="stat__label">全长</dt>
                <dd class="stat__value">{{ ship.length }} m</dd>
              </div>
              <div class="stat">
                <dt class="stat__label">型宽</dt>
                <dd class="stat__value">{{ ship.beam }} m</dd>
              </div>
              <div class="stat">
                <dt class="stat__label">吃水</dt>
                <dd class="stat__value">{{ ship.draft }} m</dd>
              </div>
            </dl>
          </div>
        </section>

        <section class="card">
          <header class="card__head">
            <h2 class="card__title">舱室风险分布</h2>
            <span class="t-label">共 {{ compartments.length }} 个</span>
          </header>
          <div class="card__body risk-list">
            <div
              v-for="c in compartments"
              :key="c.id"
              class="risk-item"
              :aria-current="c.id === selectedCompartment ? 'true' : undefined"
            >
              <span class="risk-item__name">{{ c.name }}</span>
              <span class="risk" :data-level="c.risk">{{ riskLabel(c.risk) }}</span>
              <div class="risk-item__track">
                <div
                  class="risk-item__fill"
                  :style="{
                    width: riskWidth(c.risk) + '%',
                    background: riskColor(c.risk)
                  }"
                ></div>
              </div>
            </div>
          </div>
        </section>

        <p v-if="!backendOnline" class="notice notice--warn" style="margin-top: var(--sp-3)">
          <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
          <span>后端数据源未连接，当前显示为基线读数。启动 <code>backend</code> 服务后将自动接入实时数据。</span>
        </p>
      </template>

      <!-- ═══ 实时监控 ═══ -->
      <template v-else-if="activeTab === 'monitor'">
        <div v-if="selectedCompartment == null" class="empty">
          <span class="empty__icon" aria-hidden="true">◎</span>
          <p class="empty__title">未选择舱室</p>
          <p class="empty__hint">在三维视图或舱室列表中选择一个舱室，即可查看其实时监测数据。</p>
        </div>

        <template v-else>
          <section class="card">
            <header class="card__head">
              <h2 class="card__title">{{ selected?.name }}</h2>
              <span class="tag" :class="selectedState.tagClass">
                <span class="dot" :class="{ 'dot--pulse': selectedState.active }"></span>
                {{ selectedState.label }}
              </span>
            </header>
            <div class="card__body">
              <p class="t-label" style="margin-bottom: var(--sp-3)">{{ selected?.deck }}</p>
              <p class="t-label" style="line-height: 1.7">{{ selected?.description }}</p>
            </div>
          </section>

          <section class="card">
            <header class="card__head">
              <h2 class="card__title">实时读数</h2>
              <span class="t-num">
                {{ backendOnline ? '后端在线' : '基线数据' }}
              </span>
            </header>
            <div class="card__body">
              <div
                v-for="m in METRICS"
                :key="m.key"
                class="meter"
                :class="`meter--${severityOf(m.key, reading[m.key])}`"
              >
                <div class="meter__top">
                  <span class="meter__label">{{ m.label }}</span>
                  <span class="meter__value">
                    {{ fmt(reading[m.key], THRESHOLDS[m.key].digits) }}
                    <small>{{ THRESHOLDS[m.key].unit }}</small>
                  </span>
                </div>
                <div
                  class="meter__track"
                  role="meter"
                  :aria-valuenow="Number(reading[m.key]?.toFixed?.(1) ?? 0)"
                  :aria-valuemin="0"
                  :aria-valuemax="THRESHOLDS[m.key].max"
                  :aria-label="m.label"
                >
                  <div
                    class="meter__fill"
                    :style="{ width: meterWidth(m.key, reading[m.key]) + '%' }"
                  ></div>
                </div>
              </div>
            </div>
          </section>

          <section class="card">
            <header class="card__head">
              <h2 class="card__title">采样状态</h2>
              <span class="t-num">{{ history.length }} 点</span>
            </header>
            <div class="card__body">
              <dl class="stat-grid">
                <div class="stat">
                  <dt class="stat__label">数据来源</dt>
                  <dd class="stat__value" style="font-size: var(--fs-md)">
                    <span class="tag" :class="sourceTagClass">{{ sourceLabel }}</span>
                  </dd>
                </div>
                <div class="stat">
                  <dt class="stat__label">演化引擎</dt>
                  <dd class="stat__value" style="font-size: var(--fs-md)">
                    <span class="tag" :class="engineInfo?.available ? 'tag--ok' : 'tag--danger'">
                      {{ engineInfo?.engine === 'lstm' ? 'LSTM 模型' : (engineInfo ? '未知' : '未连接') }}
                    </span>
                  </dd>
                </div>
              </dl>

              <!-- 变化速率：一眼看出态势在往哪个方向走 -->
              <div v-if="rates" class="rate-list">
                <div v-for="m in METRICS" :key="m.key" class="rate-item">
                  <span class="rate-item__label">{{ m.label }}</span>
                  <span
                    class="rate-item__value"
                    :class="rateClass(m.key, rates[m.key])"
                  >{{ rateText(m.key, rates[m.key]) }}</span>
                </div>
              </div>
              <p v-else class="t-label">变化速率需要至少两个采样点</p>

              <!-- 模型限制必须如实告知使用者，不能默默当成可信数据 -->
              <p v-if="modelDegraded" class="notice notice--danger" style="margin-top: var(--sp-3)">
                <i class="fas fa-circle-exclamation" aria-hidden="true"></i>
                <span>{{ modelDegraded }}</span>
              </p>
              <p v-if="engineLimit" class="notice notice--warn" style="margin-top: var(--sp-3)">
                <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
                <span>{{ engineLimit }}</span>
              </p>
            </div>
          </section>
        </template>
      </template>

      <!-- ═══ 智能分析 ═══ -->
      <template v-else-if="activeTab === 'ai'">
        <section class="card">
          <header class="card__head">
            <h2 class="card__title">一键态势分析</h2>
          </header>
          <div class="card__body">
            <label class="field" style="margin-bottom: var(--sp-3)">
              <span class="field__label">分析模型</span>
              <select v-model="ai.model" class="select" :disabled="!ai.models.length">
                <option v-if="!ai.models.length" :value="null">未检测到可用模型</option>
                <option v-for="m in ai.models" :key="m.id ?? m" :value="m.id ?? m">
                  {{ m.name ?? m.id ?? m }}
                </option>
              </select>
            </label>
            <button
              class="btn btn--primary btn--block"
              :disabled="ai.loading || selectedCompartment == null"
              @click="analyze"
            >
              <i class="fas" :class="ai.loading ? 'fa-spinner fa-spin' : 'fa-brain'" aria-hidden="true"></i>
              {{ ai.loading ? (ai.stage || '分析中…') : '开始分析' }}
            </button>
            <p v-if="selectedCompartment == null" class="t-label" style="margin-top: var(--sp-2)">
              请先选择舱室
            </p>
          </div>
        </section>

        <section v-if="aiResult" class="card">
          <header class="card__head">
            <h2 class="card__title">分析结果</h2>
            <span class="risk" :data-level="normalizeRisk(aiResult.riskLevel ?? aiResult.risk)">
              {{ riskLabel(aiResult.riskLevel ?? aiResult.risk) }}
            </span>
          </header>
          <div class="card__body">
            <p class="t-label" style="line-height: 1.8; margin-bottom: var(--sp-3)">
              {{ aiResult.summary || aiResult.content || '无结论' }}
            </p>
            <div v-if="aiResult.fireStage" class="ai-meta">
              <span class="ai-meta__item">阶段：<b>{{ aiResult.fireStage }}</b></span>
              <span v-if="aiResult.riskScore != null" class="ai-meta__item">
                风险分：<b>{{ aiResult.riskScore }}</b>
              </span>
              <span v-if="aiResult.provider" class="ai-meta__item ai-meta__item--src">
                {{ aiResult.provider }}
              </span>
            </div>
            <ul v-if="threats.length" class="list-block" style="margin-top: var(--sp-3)">
              <li v-for="(t, i) in threats" :key="i" class="list-block__item list-block__item--threat">
                <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>{{ t }}
              </li>
            </ul>
            <ul v-if="recommendations.length" class="list-block" style="margin-top: var(--sp-2)">
              <li v-for="(r, i) in recommendations" :key="i" class="list-block__item">
                <i class="fas fa-circle-check" aria-hidden="true"></i>{{ r }}
              </li>
            </ul>
            <ul v-if="warnings.length" class="list-block" style="margin-top: var(--sp-2)">
              <li v-for="(w, i) in warnings" :key="i" class="list-block__item list-block__item--warn">
                <i class="fas fa-circle-info" aria-hidden="true"></i>{{ w }}
              </li>
            </ul>
          </div>
        </section>

        <section v-else-if="ai.error" class="notice notice--danger">
          <i class="fas fa-circle-exclamation" aria-hidden="true"></i>
          <span>{{ ai.error }}</span>
        </section>

        <section class="card">
          <header class="card__head">
            <h2 class="card__title">与损管模型对话</h2>
            <button v-if="ai.chat.length" class="btn btn--ghost btn--sm" @click="clearChat">清空</button>
          </header>
          <div class="card__body chat">
            <div v-if="!ai.chat.length" class="empty" style="padding: var(--sp-6) 0">
              <span class="empty__icon" aria-hidden="true">◇</span>
              <p class="empty__hint">就当前舱室态势提问，例如「机库起火应如何组织灭火力量」。</p>
            </div>
            <ul v-else class="chat__list" role="log" aria-live="polite">
              <li
                v-for="(m, i) in ai.chat"
                :key="i"
                class="chat__msg"
                :class="`chat__msg--${m.role}`"
              >
                <span v-if="m.role === 'error'" class="chat__msg-icon">
                  <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
                </span>{{ m.content }}
              </li>
              <!-- 分析过程：把后端真实经过的处理阶段显示出来，
                   而不是编一段"深度思考"文案冒充模型推理 -->
              <li v-if="ai.chatLoading" class="chat__stage">
                <span class="chat__stage-title">
                  <span class="dot dot--pulse"></span> 分析过程
                </span>
                <ol v-if="ai.stages.length" class="chat__stage-list">
                  <li
                    v-for="(s, si) in ai.stages"
                    :key="si"
                    :class="s.done ? 'is-done' : 'is-active'"
                  >
                    <i class="fas" :class="s.done ? 'fa-check' : 'fa-spinner fa-spin'" aria-hidden="true"></i>
                    {{ s.text }}
                  </li>
                </ol>
              </li>
            </ul>
            <p v-if="ai.chatLoading && !ai.chat.length" class="chat__stage chat__stage--solo">
              <span class="dot dot--pulse"></span> {{ ai.stage || '正在生成…' }}
            </p>
            <!-- 快捷提问：降低使用门槛 -->
            <div v-if="!ai.chat.length" class="chat__chips">
              <button
                v-for="q in QUICK_ASKS"
                :key="q"
                class="chat__chip"
                type="button"
                :disabled="ai.chatLoading"
                @click="chatDraft = q"
              >{{ q }}</button>
            </div>
            <form class="chat__form" @submit.prevent="submitChat">
              <input
                v-model="chatDraft"
                class="input"
                type="text"
                placeholder="输入问题…"
                aria-label="对话输入"
                :disabled="ai.chatLoading"
              />
              <button class="btn btn--primary" type="submit" :disabled="!chatDraft.trim() || ai.chatLoading">
                发送
              </button>
            </form>
          </div>
        </section>
      </template>

      <!-- ═══ 舱室列表 ═══ -->
      <template v-else-if="activeTab === 'compartments'">
        <div class="compartment-list">
          <button
            v-for="c in compartments"
            :key="c.id"
            class="row"
            :aria-pressed="c.id === selectedCompartment"
            @click="$emit('select-compartment', c.id)"
          >
            <span class="compartment-swatch" :style="{ background: fireTypeOf(c).color }" aria-hidden="true"></span>
            <span class="row__main">
              <span class="row__title">
                {{ c.name }}
                <span class="t-num">{{ c.code }}</span>
                <span v-if="fireStateOf(c.id).active" class="row__sev">
                  {{ Math.round((fireStateOf(c.id).severity || 0) * 100) }}%
                </span>
              </span>
              <span class="row__meta">{{ fireTypeOf(c).label }} · {{ c.deck }}</span>
            </span>
            <span class="row__side">
              <span
                class="tag"
                :class="fireStateOf(c.id).active ? 'tag--danger' : 'tag--ok'"
              >
                <span class="dot" :class="{ 'dot--pulse': fireStateOf(c.id).active }"></span>
                {{ fireStateOf(c.id).active ? '火灾' : '正常' }}
              </span>
            </span>
          </button>
        </div>
        <p v-if="!backendOnline" class="notice notice--warn" style="margin-top: var(--sp-3)">
          <i class="fas fa-plug-circle-xmark" aria-hidden="true"></i>
          <span>后端未连接，当前读数为本地兜底模拟。</span>
        </p>
      </template>

      <!-- ═══ 火灾控制 ═══ -->
      <template v-else-if="activeTab === 'fire'">
        <div v-if="selectedCompartment == null" class="empty">
          <span class="empty__icon" aria-hidden="true">◎</span>
          <p class="empty__title">未选择舱室</p>
          <p class="empty__hint">选择一个舱室后可下发模拟指令并查看实时参数。</p>
        </div>

        <template v-else>
          <section class="card">
            <header class="card__head">
              <h2 class="card__title">{{ selected?.name }} · 模拟控制</h2>
              <span class="tag" :class="selectedState.tagClass">
                {{ selectedState.label }}
              </span>
            </header>
            <div class="card__body">
              <p v-if="lastError" class="notice notice--danger" style="margin-bottom: var(--sp-3)">
                <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
                <span>指令下发失败：{{ lastError }}</span>
              </p>
              <div class="btn-grid">
                <button
                  class="btn"
                  :class="isFireActive ? 'btn--danger' : 'btn--primary'"
                  :disabled="busy"
                  @click="$emit('toggle-fire')"
                >
                  <i class="fas" :class="isFireActive ? 'fa-fire-extinguisher' : 'fa-fire'" aria-hidden="true"></i>
                  {{ isFireActive ? '终止模拟' : '启动模拟' }}
                </button>
                <button
                  class="btn"
                  :disabled="busy || !isFireActive"
                  @click="$emit('suppress-fire')"
                >
                  <i class="fas fa-spray-can" aria-hidden="true"></i>
                  启动灭火
                </button>
                <button
                  class="btn"
                  :disabled="busy || !isFireActive"
                  @click="$emit('evacuate-crew')"
                >
                  <i class="fas fa-person-running" aria-hidden="true"></i>
                  疏散人员
                </button>
                <button
                  class="btn"
                  :disabled="busy"
                  @click="$emit('reset-fire')"
                >
                  <i class="fas fa-rotate-left" aria-hidden="true"></i>
                  重置数据
                </button>
              </div>
              <p v-if="busy" class="t-label" style="margin-top: var(--sp-3)">
                <i class="fas fa-spinner fa-spin" aria-hidden="true"></i> 指令下发中…
              </p>
            </div>
          </section>

          <section class="card">
            <header class="card__head">
              <h2 class="card__title">火灾参数</h2>
            </header>
            <div class="card__body">
              <div
                v-for="m in METRICS"
                :key="m.key"
                class="meter"
                :class="`meter--${severityOf(m.key, reading[m.key])}`"
              >
                <div class="meter__top">
                  <span class="meter__label">{{ m.label }}</span>
                  <span class="meter__value">
                    {{ fmt(reading[m.key], THRESHOLDS[m.key].digits) }}
                    <small>{{ THRESHOLDS[m.key].unit }}</small>
                  </span>
                </div>
                <div class="meter__track">
                  <div
                    class="meter__fill"
                    :style="{ width: meterWidth(m.key, reading[m.key]) + '%' }"
                  ></div>
                </div>
              </div>
            </div>
          </section>
        </template>
      </template>

      <!-- ═══ 态势预测 ═══ -->
      <template v-else-if="activeTab === 'prediction'">
        <section class="card">
          <header class="card__head">
            <h2 class="card__title">态势预测</h2>
            <span class="tag" :class="lstmTagClass">{{ lstmTagText }}</span>
          </header>
          <div class="card__body">
            <div v-if="lstm.available === false" class="notice notice--warn">
              <i class="fas fa-plug-circle-xmark" aria-hidden="true"></i>
              <span>预测服务未启动，已降级为变化率外推。启动 <code>lstm-prediction-server</code> 后将自动切回模型预测。</span>
            </div>
            <p v-else-if="lstm.error" class="notice notice--info">
              <i class="fas fa-circle-info" aria-hidden="true"></i>
              <span>{{ lstm.error }}</span>
            </p>

            <!-- 预测来源必须区分：模型预测 vs 变化率外推，数值可信度完全不同 -->
            <p class="t-label" style="line-height: 1.7">
              <span class="tag" :class="lstm.source === 'lstm' ? 'tag--ok' : 'tag--warn'">
                {{ lstm.sourceLabel || '暂无预测' }}
              </span>
            </p>
            <!-- 预测时长：损管按决策需要选 5 / 15 / 30 分钟 -->
            <div class="fc-horizon">
              <span class="fc-horizon__label">预测时长</span>
              <div class="fc-horizon__opts">
                <button
                  v-for="h in HORIZON_OPTS"
                  :key="h.v"
                  class="fc-horizon__btn"
                  :class="{ 'is-on': lstm.horizon === h.v }"
                  :disabled="lstm.running"
                  @click="setHorizon(h.v)"
                >{{ h.label }}</button>
              </div>
            </div>

            <p class="t-label" style="line-height: 1.7; margin-top: var(--sp-2)">
              <!-- 显示模型**实际**输出的时长，不是请求量。 -->
              <template v-if="lstm.actualHorizon">
                未来 <b>{{ fmtMinutes(lstm.actualHorizon) }}</b> 的温度与烟气演化趋势
              </template>
              <template v-else>暂无预测</template>
              <template v-if="lstm.predictions.length">
                · 共 {{ lstm.predictions.length }} 个预测点
              </template>
            </p>

            <!-- 关键：必须让使用者知道哪一段是模型、哪一段是物理外推 -->
            <div v-if="lstm.analysis && lstm.analysis.lstmSeconds != null" class="fc-basis">
              <div class="fc-basis__row">
                <span class="fc-basis__tag fc-basis__tag--lstm">模型推演</span>
                <span class="fc-basis__v">{{ lstm.analysis.lstmSeconds }}s</span>
              </div>
              <div class="fc-basis__row">
                <span class="fc-basis__tag fc-basis__tag--growth">增长律外推</span>
                <span class="fc-basis__v">{{ fmtMinutes(lstm.analysis.growthSeconds) }}</span>
              </div>
              <p class="fc-basis__note">
                <template v-if="lstm.analysis.limitCycleDetected">
                  自回归外推在 {{ lstm.analysis.limitCycleAtSeconds }}s 后收敛到周期解，
                  该点之后已切换为舱室火灾增长律（SOLAS II-2）外推，非模型输出。
                </template>
                <template v-else>
                  超出训练时域的部分属分布外外推，可信度较低。
                </template>
              </p>
            </div>

            <!-- 损管决策真正需要的是"接下来会怎样"，不是一条曲线 -->
            <div v-if="lstm.analysis && lstm.analysis.phase" class="fc-analysis">
              <div class="fc-analysis__row">
                <span class="fc-analysis__k">发展阶段</span>
                <span class="fc-analysis__v" :data-phase="lstm.analysis.phase">
                  {{ phaseLabel(lstm.analysis.phase) }}
                </span>
              </div>
              <div class="fc-analysis__row">
                <span class="fc-analysis__k">预测峰值</span>
                <span class="fc-analysis__v">
                  {{ Math.round(lstm.analysis.peakTemperature) }}℃
                </span>
              </div>
              <div class="fc-analysis__row">
                <span class="fc-analysis__k">达峰时间</span>
                <span class="fc-analysis__v">
                  {{ fmtMinutes(lstm.analysis.timeToPeakSeconds) }}
                </span>
              </div>
              <div class="fc-analysis__row">
                <span class="fc-analysis__k">末态温度</span>
                <span class="fc-analysis__v">
                  {{ Math.round(lstm.predictions[lstm.predictions.length - 1]?.temperature || 0) }}℃
                </span>
              </div>
              <div class="fc-analysis__row">
                <span class="fc-analysis__k">增长指数</span>
                <span class="fc-analysis__v">
                  t<sup>{{ (lstm.analysis.growthExponent ?? 0).toFixed(1) }}</sup>
                </span>
              </div>
            </div>

            <!-- 超出训练时域的部分必须如实标出，不能让人以为是可信的模型输出 -->
            <p v-if="lstm.analysis?.trainedHorizonSeconds && lstm.actualHorizon > lstm.analysis.trainedHorizonSeconds * 2"
               class="notice notice--warn" style="margin-top: var(--sp-2)">
              <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
              <span>
                模型仅在 {{ lstm.analysis.trainedHorizonSeconds }} 秒训练数据内可靠；
                曲线中灰色段为增长律外推，用于判断火势走向，不等同于模型预测。
              </span>
            </p>
            <p v-if="lstm.updatedAt" class="t-num" style="margin-top: var(--sp-2)">
              更新于 {{ timeText(lstm.updatedAt) }}
            </p>
          </div>
        </section>

        <section v-if="selectedCompartment != null" class="card">
          <header class="card__head">
            <h2 class="card__title">实测与预测趋势</h2>
            <span class="t-num">{{ history.length }} 点实测</span>
          </header>
          <div class="card__body">
            <TrendChart
              :series="history"
              :predictions="lstm.predictions"
              :prediction-source="lstm.source"
              :trained-horizon-seconds="lstm.analysis?.trainedHorizonSeconds"
              :lstm-seconds="lstm.analysis?.lstmSeconds"
            />
          </div>
        </section>
      </template>

      <!-- ═══ 告警中心 ═══ -->
      <template v-else-if="activeTab === 'alerts'">
        <div class="alert-toolbar">
          <span class="t-label">
            {{ activeAlerts.length }} 条未消解
            <template v-if="unreadAlerts"> · {{ unreadAlerts }} 条待确认</template>
          </span>
          <button class="btn btn--ghost btn--sm" @click="refreshAlerts">
            <i class="fas fa-rotate" aria-hidden="true"></i> 刷新
          </button>
        </div>

        <div v-if="!activeAlerts.length" class="empty">
          <span class="empty__icon" aria-hidden="true">✓</span>
          <p class="empty__title">暂无告警</p>
          <p class="empty__hint">告警由后端按温度、烟雾、氧气与一氧化碳阈值自动升降级，无需手工配置。</p>
        </div>

        <ul v-else class="alert-list">
          <li
            v-for="a in activeAlerts"
            :key="a.id"
            class="alert-item"
            :data-level="a.level"
            :class="{ 'is-ack': a.acknowledged }"
          >
            <div class="alert-item__body">
              <div class="alert-item__head">
                <span class="risk" :data-level="a.level">{{ riskLabel(a.level) }}</span>
                <button
                  v-if="a.compartmentId"
                  class="btn btn--ghost btn--sm alert-item__jump"
                  @click="$emit('select-compartment', a.compartmentId)"
                >
                  <i class="fas fa-location-arrow" aria-hidden="true"></i>
                  定位
                </button>
              </div>
              <p class="alert-item__msg">{{ a.message || a.title }}</p>
              <p class="alert-item__time">{{ timeText(a.time) }}</p>
              <div class="alert-item__actions">
                <button
                  class="btn btn--sm"
                  :disabled="a.acknowledged"
                  @click="ackAlert(a.id)"
                >{{ a.acknowledged ? '已确认' : '确认' }}</button>
                <button
                  class="btn btn--sm btn--primary"
                  @click="resolveAlert(a.id)"
                >消解</button>
              </div>
            </div>
          </li>
        </ul>
      </template>
    </div>
  </section>
</template>

<script setup>
import { computed, ref, useId } from 'vue'
import { COMPARTMENTS, RISK_LEVELS, SHIP, getCompartmentById, getFireType } from '@/config/shipLayout'
import { useOpsConsole } from '@/composables/useOpsConsole'
import TrendChart from '@/components/TrendChart.vue'

const props = defineProps({
  side: { type: String, default: 'right' },
  panelTitle: { type: String, default: '控制台' },
  visibleTabs: { type: Array, default: () => [] },
  defaultTab: { type: String, default: '' },
  selectedCompartment: { type: Number, default: null },
  isFireActive: { type: Boolean, default: false },
  fireIntensity: { type: Number, default: 0 },
  busy: { type: Boolean, default: false },
  isOpen: { type: Boolean, default: false }
})

const uid = useId()
const collapsed = ref(false)
const chatDraft = ref('')

const ALL_TABS = [
  { id: 'ship', name: '舰船信息', icon: 'fa-ship' },
  { id: 'compartments', name: '舱室列表', icon: 'fa-layer-group' },
  { id: 'fire', name: '火灾控制', icon: 'fa-fire' },
  { id: 'monitor', name: '实时监控', icon: 'fa-gauge-high' },
  { id: 'prediction', name: '态势预测', icon: 'fa-chart-line' },
  { id: 'alerts', name: '告警中心', icon: 'fa-bell' },
  { id: 'ai', name: '智能分析', icon: 'fa-brain' }
]

const tabs = computed(() => ALL_TABS.filter(t => props.visibleTabs.includes(t.id)))
const activeTab = ref(
  props.defaultTab && props.visibleTabs.includes(props.defaultTab)
    ? props.defaultTab
    : tabs.value[0]?.id || 'ship'
)

/* ── 标签页键盘导航 ── */
function onTabKey(e, id) {
  const list = tabs.value
  const i = list.findIndex(t => t.id === id)
  let next = null
  if (e.key === 'ArrowRight') next = list[(i + 1) % list.length]
  else if (e.key === 'ArrowLeft') next = list[(i - 1 + list.length) % list.length]
  else if (e.key === 'Home') next = list[0]
  else if (e.key === 'End') next = list[list.length - 1]
  if (!next) return
  e.preventDefault()
  activeTab.value = next.id
  requestAnimationFrame(() => {
    document.getElementById(`${uid}-tab-${next.id}`)?.focus()
  })
}

/* ── 共享状态 ── */
const ops = useOpsConsole()
const {
  readings, history, historyRates: rates, activeAlerts, unreadAlerts,
  engineInfo, backendOnline, lastError, lstm, ai, THRESHOLDS, METRICS,
  fmt, severityOf, analyze, sendChat, clearChat, ackAlert, resolveAlert, refreshAlerts
} = ops

const compartments = COMPARTMENTS
const ship = SHIP

const selected = computed(() => getCompartmentById(props.selectedCompartment))
// ⚠ <script setup> 里 props 是响应式对象，props.selectedCompartment 是**普通值**
// 而不是 ref，写 .value 会得到 undefined。旧代码这里写成 props.selectedCompartment.value，
// 于是 ai.results[undefined] 永远取不到分析结果 —— 接口明明成功返回了，
// 界面上却什么都没显示。
const reading = computed(() => readings[props.selectedCompartment] || ops.BASELINE)
const aiResult = computed(() => ai.results[props.selectedCompartment] || null)
const isFireActive = computed(() => props.isFireActive)
const busy = computed(() => props.busy)

/** 数据来源如实标注：后端 LSTM / 本地兜底 / 基线 */
const sourceLabel = computed(() => {
  const src = reading.value?.source
  if (src === 'lstm') return 'LSTM 引擎'
  if (src === 'fallback') return '本地兜底'
  return '基线值'
})
const SOURCE_TAG = { lstm: 'tag--ok', fallback: 'tag--warn' }
const sourceTagClass = computed(() => SOURCE_TAG[reading.value?.source] || '')

/**
 * 还没换成新配方（v6）的舱室：曲线看着平滑收敛、不报警，
 * 但实测会塌缩成常数。必须显式降级提示，不能让值班员当成结论。
 * 数据来源：tools/regress_online.py（拿真实 FDS 轨迹打 5001 与真值比对）
 *   电站间 v3  300 秒末 379.5℃±0.1，真值 43.8℃±8.0
 *   士兵住舱 v3 300 秒末 239.4℃，真值 60.4℃
 *   机库   v3  300 秒末 453.5℃±7.1，真值 189.4℃±65.1
 */
const modelDegraded = computed(() => {
  const name = selected.value?.name
  if (!name) return null
  const cfg = engineInfo.value?.lstmConfig || {}
  const table = cfg.degradedCompartments || {}
  let hit = table[name]
  if (!hit) {
    for (const [k, v] of Object.entries(table)) {
      if (k.includes(name) || name.includes(k)) { hit = v; break }
    }
  }
  if (!hit) return null
  const src = hit.source || '旧'
  const h = hit.reliableHorizonSeconds
  const tail = Number.isFinite(h) && h > 0
    ? `单次预测仅 ${h} 秒可用` : '单次预测窗口极短'
  return `${name} 当前仍是 ${src} 配方模型（${tail}）：`
    + `自回归外推会收敛到常数平台，温度/气体曲线只能当作示意，不能作为处置依据。`
})

/** 火灾严重度里带模型能力标记 */
const engineLimit = computed(() => {
  const info = engineInfo.value
  if (!info) return null
  const noTemp = info.limitations?.noTemperatureSignal || []
  if (noTemp.includes(selected.value?.name)) {
    return `${selected.value.name} 的温度不在模型驱动范围内 —— 该舱室训练数据中温度全程恒定，温度不可作为判断依据。`
  }
  // 把"多长的预测可信"和"总共多少训练数据"分开说清楚，
  // 这两个数不是一回事：单次预测窗口远短于训练轨迹总长。
  const horizon = info.trainedHorizonSeconds
  const step = info.stepSeconds
  const parts = [`模型单次预测窗口约 ${info.predictionWindowSeconds ?? '—'}s`]
  if (step) parts.push(`时间步长 ${step}s`)
  parts.push(`训练轨迹总长仅 ${horizon}s，累计外推超过这个长度后属于分布外外推`)
  return parts.join(' · ')
})

const selectedState = computed(() => {
  const active = props.isFireActive
  return {
    active,
    label: active ? '火灾模拟中' : '系统待机',
    tagClass: active ? 'tag--danger' : 'tag--ok'
  }
})

const lstmTagClass = computed(() =>
  lstm.available === null ? '' : lstm.available ? 'tag--ok' : 'tag--danger'
)
const lstmTagText = computed(() => {
  if (lstm.available === null) return '检测中'
  return lstm.available ? '服务在线' : '服务离线'
})

const recommendations = computed(() => {
  const r = aiResult.value
  if (!r) return []
  return r.recommendations || r.advice || r.suggestions || []
})

/** 快捷提问：比空白输入框更容易让人知道能问什么 */
const QUICK_ASKS = [
  '当前火势处于什么阶段？',
  '应该先灭火还是先撤离？',
  '相邻舱室会受到多大影响？',
  '给出下一步处置顺序'
]

const asList = v => (Array.isArray(v) ? v : v ? [v] : [])
const threats = computed(() => asList(aiResult.value?.threats))
const warnings = computed(() => asList(aiResult.value?.warnings))

/** 火灾阶段 -> 中文标签。阶段判定来自温升速率，不靠模型自由描述。 */
const PHASE_LABEL = {
  growth: '增长期（火势正在扩大）',
  developing: '缓慢发展',
  'fully-developed': '充分发展（趋于稳定）',
  decaying: '衰减期（火势回落）',
  unknown: '数据不足'
}
const phaseLabel = p => PHASE_LABEL[p] || p

/** 预测时长选项：损管按决策窗口选 */
const HORIZON_OPTS = [
  { v: 300, label: '5 分钟' },
  { v: 900, label: '15 分钟' },
  { v: 1800, label: '30 分钟' }
]
const setHorizon = v => { ops.lstm.horizon = v; ops.runPrediction() }

/** 秒 -> "15 分钟" / "45 秒" */
function fmtMinutes(sec) {
  const s = Number(sec)
  if (!Number.isFinite(s) || s <= 0) return '--'
  if (s < 90) return `${Math.round(s)} 秒`
  const m = s / 60
  return (m >= 10 ? Math.round(m) : Math.round(m * 10) / 10) + ' 分钟'
}

/* ── 展示辅助 ── */
const fireTypeOf = c => getFireType(c.fireType)
/** 火灾状态直接取后端状态机，不再用字符串正则猜 */
const fireStateOf = id => ops.fires[id] || { active: false, severity: 0 }

const riskLabel = k => (RISK_LEVELS[k] || RISK_LEVELS.unknown).label
const riskColor = k => (RISK_LEVELS[k] || RISK_LEVELS.unknown).color
const riskWidth = k => ((RISK_LEVELS[k] || RISK_LEVELS.unknown).order / 4) * 100

function normalizeRisk(v) {
  const s = String(v || '').toLowerCase()
  if (['critical', 'danger', '危急'].includes(s)) return 'critical'
  if (['high', '高'].includes(s)) return 'high'
  if (['medium', 'medium_risk', '中'].includes(s)) return 'medium'
  if (['low', '低'].includes(s)) return 'low'
  return 'unknown'
}

function meterWidth(key, value) {
  const t = THRESHOLDS[key]
  const v = Number(value)
  if (!t || !Number.isFinite(v)) return 0
  return Math.max(0, Math.min(100, (v / t.max) * 100))
}

/* ---- 变化速率展示 ---- */
function rateClass(key, v) {
  const t = THRESHOLDS[key]
  const n = Number(v)
  if (!t || !Number.isFinite(n) || Math.abs(n) < 0.05) return 'rate-item__value--flat'
  const rising = t.inverse ? n < 0 : n > 0
  // 对温度/烟雾/CO 而言上升是恶化；对氧气而言下降才是恶化
  return rising ? 'rate-item__value--bad' : 'rate-item__value--good'
}

function rateText(key, v) {
  const t = THRESHOLDS[key]
  const n = Number(v)
  if (!t || !Number.isFinite(n)) return '--'
  if (Math.abs(n) < 0.05) return '基本稳定'
  const digits = Math.abs(n) >= 100 ? 0 : 1
  return `${n > 0 ? '+' : ''}${n.toFixed(digits)} ${t.unit}/min`
}

function timeText(ts) {
  if (!ts) return '--'
  return new Date(ts).toLocaleTimeString('zh-CN', { hour12: false })
}

function submitChat() {
  const text = chatDraft.value.trim()
  if (!text) return
  chatDraft.value = ''
  sendChat(text)
}
</script>

<style scoped>
/* 本组件只做布局补充，不重定义设计系统中的同名类 */

/* 头部必须是 flex 行：设计系统的 .panel__head 只给了 padding 和边框，
   没有 display，于是标题和收起按钮会竖着堆叠，按钮掉到左下角。 */
.panel__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-2);
}
.panel__title { min-width: 0; }
.panel__title > span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.panel__collapse { flex: none; padding: 5px 8px; }
.panel__collapse .rot { transform: rotate(180deg); }

/* 折叠态：只留头部，面板高度收到头部大小。
   注意与设计系统的 .panel--collapsed（整个面板滑出屏幕）是两回事，
   后者由 myIndex 的抽屉开关控制，这里只折叠内容。 */
.panel.is-collapsed {
  bottom: auto;
  height: auto;
  max-height: calc(100vh - var(--topbar-h) - var(--panel-gap));
}

.risk-list { display: flex; flex-direction: column; gap: var(--sp-3); }
.risk-item {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: center;
  gap: 4px var(--sp-2);
}
.risk-item__name { font-size: var(--fs-base); color: var(--text-primary); }
.risk-item__track {
  grid-column: 1 / -1;
  height: 4px;
  border-radius: var(--r-full);
  background: rgba(148, 197, 255, 0.12);
  overflow: hidden;
}
.risk-item__fill { height: 100%; border-radius: var(--r-full); transition: width var(--transition-base); }

.compartment-list { display: flex; flex-direction: column; gap: var(--sp-1); }
.compartment-swatch {
  width: 4px;
  align-self: stretch;
  min-height: 32px;
  border-radius: var(--r-full);
  flex: none;
}
/* 行内火灾强度徽标 */
.row__sev {
  margin-left: var(--sp-2);
  padding: 1px 6px;
  border-radius: var(--r-full);
  font-size: var(--fs-xs);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: #fca5a5;
  background: var(--c-danger-soft);
}

/* ── 变化速率 ── */
.rate-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-top: var(--sp-3);
  padding-top: var(--sp-3);
  border-top: 1px solid var(--border-subtle);
}
.rate-item {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--sp-3);
  padding: 3px 0;
}
.rate-item__label { font-size: var(--fs-sm); color: var(--text-secondary); }
.rate-item__value {
  font-size: var(--fs-sm);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.rate-item__value--bad { color: var(--c-danger); }
.rate-item__value--good { color: var(--c-success); }
.rate-item__value--flat { color: var(--text-muted); font-weight: 500; }

.list-block { display: flex; flex-direction: column; gap: var(--sp-2); }
.list-block__item {
  display: flex;
  gap: var(--sp-2);
  font-size: var(--fs-sm);
  line-height: 1.7;
  color: var(--text-secondary);
}
.list-block__item .fas { color: var(--c-success); margin-top: 4px; flex: none; }
.list-block__item--threat .fas { color: var(--c-danger); }
.list-block__item--warn .fas { color: var(--c-warning); }

/* 预测时长选择 */
.fc-horizon {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  margin-top: var(--sp-2);
}
.fc-horizon__label { font-size: var(--fs-xs); color: var(--text-muted); flex: none; }
.fc-horizon__opts { display: flex; gap: 3px; }
.fc-horizon__btn {
  padding: 3px 10px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-full);
  background: transparent;
  color: var(--text-secondary);
  font-size: var(--fs-xs);
  font-family: inherit;
  cursor: pointer;
  transition: all var(--transition-fast);
}
.fc-horizon__btn:hover:not(:disabled) { border-color: rgba(148, 197, 255, 0.3); color: var(--text-primary); }
.fc-horizon__btn.is-on {
  background: var(--c-primary);
  border-color: var(--c-primary);
  color: #fff;
  font-weight: 600;
}
.fc-horizon__btn:disabled { opacity: 0.45; cursor: not-allowed; }

/* 预测分段来源 —— 必须让使用者一眼看出哪段是模型、哪段是外推 */
.fc-basis {
  margin-top: var(--sp-2);
  padding: var(--sp-2);
  border-radius: var(--r-md);
  background: rgba(148, 197, 255, 0.05);
  border: 1px solid var(--border-subtle);
}
.fc-basis__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-2);
  padding: 1px 0;
  font-size: var(--fs-xs);
}
.fc-basis__tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--fs-xs);
  font-weight: 600;
  padding: 1px 7px;
  border-radius: var(--r-full);
}
.fc-basis__tag::before {
  content: '';
  width: 8px; height: 8px;
  border-radius: 2px;
}
.fc-basis__tag--lstm { color: var(--c-success); }
.fc-basis__tag--lstm::before { background: var(--c-success); }
.fc-basis__tag--growth { color: var(--c-warning); }
.fc-basis__tag--growth::before { background: var(--c-warning); }
.fc-basis__v {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  color: var(--text-primary);
  font-weight: 600;
}
.fc-basis__note {
  margin: var(--sp-1) 0 0;
  font-size: var(--fs-xs);
  line-height: 1.6;
  color: var(--text-muted);
}

/* 预测分析摘要：阶段 / 峰值 / 达峰时间 —— 损管要的是结论，不是曲线 */
.fc-analysis {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 4px var(--sp-2);
  margin-top: var(--sp-3);
  padding: var(--sp-2) var(--sp-3);
  border-radius: var(--r-md);
  background: rgba(148, 197, 255, 0.05);
  border: 1px solid var(--border-subtle);
}
.fc-analysis__row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--sp-2);
  font-size: var(--fs-xs);
  min-width: 0;
}
.fc-analysis__k { color: var(--text-muted); flex: none; }
.fc-analysis__v {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  color: var(--text-primary);
  font-weight: 600;
  text-align: right;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.fc-analysis__v[data-phase='growth'] { color: var(--c-danger); }
.fc-analysis__v[data-phase='decaying'] { color: var(--c-success); }
.fc-analysis__v[data-phase='fully-developed'] { color: var(--c-warning); }

/* 分析结果的元信息行 */
.ai-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-2);
  font-size: var(--fs-xs);
  color: var(--text-muted);
}
.ai-meta__item b { color: var(--text-primary); font-weight: 600; }
.ai-meta__item--src {
  margin-left: auto;
  padding: 1px 7px;
  border-radius: var(--r-full);
  background: var(--bg-elevated);
  border: 1px solid var(--border-subtle);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.chat { display: flex; flex-direction: column; gap: var(--sp-3); }
.chat__list { display: flex; flex-direction: column; gap: var(--sp-2); max-height: 280px; overflow-y: auto; }
.chat__msg {
  padding: var(--sp-2) var(--sp-3);
  border-radius: var(--r-md);
  font-size: var(--fs-sm);
  line-height: 1.7;
  word-break: break-word;
  white-space: pre-wrap;
}
.chat__msg--user {
  align-self: flex-end;
  max-width: 85%;
  background: var(--c-primary-soft);
  color: var(--text-primary);
}
.chat__msg--assistant {
  align-self: flex-start;
  max-width: 92%;
  background: var(--bg-elevated);
  color: var(--text-secondary);
}
.chat__msg--error {
  align-self: flex-start;
  background: var(--c-danger-soft);
  color: #fca5a5;
}
.chat__form { display: flex; gap: var(--sp-2); }
.chat__form .input { flex: 1 1 auto; }

/* 失败消息 */
.chat__msg--error {
  align-self: flex-start;
  max-width: 94%;
  background: var(--c-danger-soft);
  color: #fca5a5;
}
.chat__msg-icon { margin-right: 6px; }

/* 分析过程：展示后端真实经过的阶段 */
.chat__stage {
  align-self: flex-start;
  width: 100%;
  padding: var(--sp-2) var(--sp-3);
  border-radius: var(--r-md);
  background: rgba(148, 197, 255, 0.06);
  border: 1px solid var(--border-subtle);
  font-size: var(--fs-xs);
  color: var(--text-secondary);
}
.chat__stage--solo { margin-top: var(--sp-2); }
.chat__stage-title {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--text-muted);
  margin-bottom: 4px;
}
.chat__stage-list {
  margin: 0;
  padding-left: 4px;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.chat__stage-list li {
  display: flex;
  align-items: center;
  gap: 6px;
  opacity: 0.55;
  transition: opacity var(--transition-fast);
}
.chat__stage-list li .fas { width: 11px; font-size: 10px; }
.chat__stage-list li.is-active { opacity: 1; color: var(--text-primary); }
.chat__stage-list li.is-done { opacity: 0.8; color: var(--c-success); }

/* 快捷提问 */
.chat__chips { display: flex; flex-wrap: wrap; gap: 4px; margin-top: var(--sp-2); }
.chat__chip {
  padding: 4px 9px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--r-full);
  background: transparent;
  color: var(--text-secondary);
  font-size: var(--fs-xs);
  font-family: inherit;
  cursor: pointer;
  transition: border-color var(--transition-fast), color var(--transition-fast);
}
.chat__chip:hover:not(:disabled) {
  border-color: rgba(148, 197, 255, 0.3);
  color: var(--text-primary);
}
.chat__chip:disabled { opacity: 0.45; cursor: not-allowed; }

.alert-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-2);
  margin-bottom: var(--sp-3);
}
.alert-list { display: flex; flex-direction: column; }
.alert-item__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-2);
  margin-bottom: var(--sp-2);
}
.alert-item__jump { flex: none; }
/* 已确认的告警降低视觉权重，但保留在列表中直到消解 */
.alert-item.is-ack { opacity: 0.62; }
.alert-item.is-ack .alert-item__msg { color: var(--text-secondary); }

.meter__value small { font-size: var(--fs-xs); font-weight: 500; opacity: 0.75; }
</style>
