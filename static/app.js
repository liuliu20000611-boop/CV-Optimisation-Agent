import { computed, createApp, ref } from "https://cdn.jsdelivr.net/npm/vue@3.5.13/dist/vue.esm-browser.js";
import {
  downloadByUrl,
  exportResumeFile,
  fetchTexTemplates,
  postSse,
} from "./sse.js";

createApp({
  setup() {
    const resume = ref("");
    const jd = ref("");
    const uploadHint = ref("");

    const analysisStream = ref("");
    const analysisResult = ref(null);
    const analysisMeta = ref({});
    const analysisErr = ref("");
    const analyzing = ref(false);

    const feedbackAnalysis = ref("");

    const optStream = ref("");
    const optimized = ref("");
    const optErr = ref("");
    const optimizing = ref(false);
    const optMeta = ref({});

    const feedbackRefine = ref("");
    const refineErr = ref("");
    const refining = ref(false);
    const refineMeta = ref({});

    const confirmOptimize = ref(false);
    const exportBusy = ref("");
    const templates = ref([]);
    const templatesLoading = ref(false);
    const templateErr = ref("");
    const selectedTemplateId = ref("");
    const templateBusy = ref(false);
    const templateMsg = ref("");
    const rewriteProgress = ref(0);
    const rewriteProgressText = ref("");
    const rewritePreviewUrls = ref([]);
    const rewriteZipUrl = ref("");
    const templateFeedback = ref("");
    const currentRewriteJobId = ref("");
    let rewriteTimer = null;

    function resetOptimizeUi() {
      optStream.value = "";
      optimized.value = "";
      optErr.value = "";
      optMeta.value = {};
      confirmOptimize.value = false;
    }

    async function onUpload(e) {
      const input = e.target;
      const file = input.files?.[0];
      if (!file) return;
      uploadHint.value = "";
      const fd = new FormData();
      fd.append("file", file);
      try {
        const res = await fetch("/api/upload-resume", { method: "POST", body: fd });
        const data = await res.json();
        if (!res.ok) {
          uploadHint.value = typeof data.detail === "string" ? data.detail : "上传失败";
          return;
        }
        if (data.content) resume.value = data.content;
        if (data.warnings?.length) uploadHint.value = data.warnings.join("；");
      } catch {
        uploadHint.value = "网络错误，请重试";
      }
      input.value = "";
    }

    function handleSseError(ev, fallback) {
      if (ev.type === "error" && typeof ev.message === "string") {
        fallback(ev.message);
        return true;
      }
      return false;
    }

    async function runAnalyze() {
      analysisStream.value = "";
      analysisResult.value = null;
      analysisMeta.value = {};
      analysisErr.value = "";
      analyzing.value = true;
      try {
        await postSse("/api/analyze/stream", { resume: resume.value, jd: jd.value }, (ev) => {
          if (handleSseError(ev, (m) => (analysisErr.value = m))) return;
          if (ev.type === "delta" && typeof ev.text === "string") analysisStream.value += ev.text;
          if (ev.type === "done" && ev.result && typeof ev.result === "object") {
            const r = ev.result;
            analysisResult.value = {
              highlights: Array.isArray(r.highlights) ? r.highlights : [],
              gaps: Array.isArray(r.gaps) ? r.gaps : [],
              suggestions: Array.isArray(r.suggestions) ? r.suggestions : [],
            };
            analysisMeta.value = { cached: ev.cached === true, fallback: ev.fallback === true };
          }
        });
        if (!analysisResult.value && !analysisErr.value) analysisErr.value = "未收到完整分析结果";
      } catch (e) {
        analysisErr.value = e instanceof Error ? e.message : "分析请求失败";
      } finally {
        analyzing.value = false;
      }
    }

    async function runReanalyze() {
      const prev = analysisResult.value;
      if (!prev) return;
      analysisStream.value = "";
      analysisErr.value = "";
      analyzing.value = true;
      const body = {
        resume: resume.value,
        jd: jd.value,
        highlights: prev.highlights,
        gaps: prev.gaps,
        suggestions: prev.suggestions,
        user_feedback: feedbackAnalysis.value.trim() || null,
      };
      try {
        await postSse("/api/reanalyze/stream", body, (ev) => {
          if (handleSseError(ev, (m) => (analysisErr.value = m))) return;
          if (ev.type === "delta" && typeof ev.text === "string") analysisStream.value += ev.text;
          if (ev.type === "done" && ev.result && typeof ev.result === "object") {
            const r = ev.result;
            analysisResult.value = {
              highlights: Array.isArray(r.highlights) ? r.highlights : [],
              gaps: Array.isArray(r.gaps) ? r.gaps : [],
              suggestions: Array.isArray(r.suggestions) ? r.suggestions : [],
            };
            analysisMeta.value = { fallback: ev.fallback === true };
          }
        });
        if (!analysisResult.value && !analysisErr.value) analysisErr.value = "未收到完整分析结果";
      } catch (e) {
        analysisErr.value = e instanceof Error ? e.message : "返修分析失败";
      } finally {
        analyzing.value = false;
      }
    }

    async function runOptimize() {
      if (!analysisResult.value || !confirmOptimize.value) return;
      optErr.value = "";
      optStream.value = "";
      optMeta.value = {};
      optimizing.value = true;
      const body = {
        resume: resume.value,
        jd: jd.value,
        highlights: analysisResult.value.highlights,
        gaps: analysisResult.value.gaps,
        suggestions: analysisResult.value.suggestions,
      };
      try {
        await postSse("/api/optimize/stream", body, (ev) => {
          if (handleSseError(ev, (m) => (optErr.value = m))) return;
          if (ev.type === "delta" && typeof ev.text === "string") optStream.value += ev.text;
          if (ev.type === "done" && typeof ev.text === "string") {
            optimized.value = ev.text;
            optMeta.value = { fallback: ev.fallback === true };
          }
        });
        if (!optimized.value && !optErr.value) optErr.value = "未收到优化正文";
      } catch (e) {
        optErr.value = e instanceof Error ? e.message : "优化失败";
      } finally {
        optimizing.value = false;
      }
    }

    async function runRefine() {
      if (!analysisResult.value || !optimized.value.trim()) return;
      refineErr.value = "";
      optStream.value = "";
      optMeta.value = {};
      refining.value = true;
      const body = {
        resume: resume.value,
        jd: jd.value,
        highlights: analysisResult.value.highlights,
        gaps: analysisResult.value.gaps,
        suggestions: analysisResult.value.suggestions,
        optimized_resume: optimized.value,
        user_feedback: feedbackRefine.value.trim() || null,
      };
      try {
        await postSse("/api/refine-optimize/stream", body, (ev) => {
          if (handleSseError(ev, (m) => (refineErr.value = m))) return;
          if (ev.type === "delta" && typeof ev.text === "string") optStream.value += ev.text;
          if (ev.type === "done" && typeof ev.text === "string") {
            optimized.value = ev.text;
            refineMeta.value = { fallback: ev.fallback === true };
          }
        });
        if (!optimized.value && !refineErr.value) refineErr.value = "未收到返修正文";
      } catch (e) {
        refineErr.value = e instanceof Error ? e.message : "返修失败";
      } finally {
        refining.value = false;
      }
    }

    async function doExport(fmt) {
      const text = optimized.value.trim();
      if (!text) return;
      exportBusy.value = fmt;
      try {
        await exportResumeFile(fmt, text);
      } catch (e) {
        optErr.value = e instanceof Error ? e.message : "导出失败";
      } finally {
        exportBusy.value = "";
      }
    }

    async function loadTemplates() {
      templatesLoading.value = true;
      templateErr.value = "";
      try {
        const data = await fetchTexTemplates();
        templates.value = Array.isArray(data) ? data : [];
        if (!selectedTemplateId.value && templates.value.length) {
          selectedTemplateId.value = templates.value[0].id;
        }
      } catch (e) {
        templateErr.value = e instanceof Error ? e.message : "模板加载失败";
      } finally {
        templatesLoading.value = false;
      }
    }

    function onSelectTemplate(templateId) {
      if (selectedTemplateId.value === templateId) return;
      selectedTemplateId.value = templateId;
      // 切模板时清理上一模板上下文，避免“跨模板返修”导致编译失败
      rewritePreviewUrls.value = [];
      rewriteZipUrl.value = "";
      templateFeedback.value = "";
      templateMsg.value = "";
      templateErr.value = "";
      rewriteProgress.value = 0;
      rewriteProgressText.value = "";
      currentRewriteJobId.value = "";
      stopRewriteCountdown();
    }

    function stopRewriteCountdown() {
      if (rewriteTimer) {
        clearInterval(rewriteTimer);
        rewriteTimer = null;
      }
    }

    function startRewriteCountdown(fromPercent, toPercent, seconds, label) {
      stopRewriteCountdown();
      const start = Date.now();
      const from = Math.max(0, Math.min(100, fromPercent));
      const to = Math.max(from, Math.min(100, toPercent));
      rewriteProgress.value = from;
      rewriteTimer = setInterval(() => {
        const elapsed = (Date.now() - start) / 1000;
        const ratio = Math.min(1, elapsed / Math.max(1, seconds));
        rewriteProgress.value = Math.round(from + (to - from) * ratio);
        rewriteProgressText.value = `${label}（处理中）`;
        if (ratio >= 1) {
          stopRewriteCountdown();
        }
      }, 250);
    }

    async function runTemplateRewrite() {
      const text = optimized.value.trim();
      if (!selectedTemplateId.value) {
        templateErr.value = "请先选择模板";
        return;
      }
      if (!text) {
        templateErr.value = "请先在「2. 生成优化简历」中得到优化稿，再进行模板改写";
        return;
      }
      templateBusy.value = true;
      stopRewriteCountdown();
      rewriteProgress.value = 0;
      rewriteProgressText.value = "准备改写…";
      templateErr.value = "";
      templateMsg.value = "";
      rewritePreviewUrls.value = [];
      rewriteZipUrl.value = "";
      try {
        const estLlmSec = Math.max(15, Math.min(70, Math.round(text.length / 220)));
        const estCompileSec = Math.max(8, Math.min(45, Math.round(text.length / 480)));
        startRewriteCountdown(5, 55, estLlmSec, "模型改写中");
        await postSse("/api/template-rewrite/stream", {
          template_id: selectedTemplateId.value,
          optimized_resume: text,
          user_feedback: templateFeedback.value.trim() || null,
          previous_job_id: currentRewriteJobId.value || null,
        }, (ev) => {
          if (ev.type === "error" && typeof ev.message === "string") {
            stopRewriteCountdown();
            templateErr.value = ev.message;
            return;
          }
          if (ev.type === "progress") {
            if (ev.stage === "prepare") {
              startRewriteCountdown(Math.max(rewriteProgress.value, 10), 55, estLlmSec, "模型改写中");
              return;
            }
            if (ev.stage === "llm_done") {
              startRewriteCountdown(65, 92, estCompileSec, "LaTeX 编译中");
              return;
            }
            if (ev.stage === "compiled") {
              stopRewriteCountdown();
              rewriteProgress.value = 95;
              rewriteProgressText.value = "编译完成，正在打包…";
              return;
            }
            if (typeof ev.percent === "number") rewriteProgress.value = ev.percent;
            if (typeof ev.message === "string") rewriteProgressText.value = ev.message;
            return;
          }
          if (ev.type === "done") {
            stopRewriteCountdown();
            rewriteProgress.value = 100;
            rewriteProgressText.value = "改写与编译完成";
            if (typeof ev.zip_download_url === "string") rewriteZipUrl.value = ev.zip_download_url;
            if (typeof ev.job_id === "string") currentRewriteJobId.value = ev.job_id;
            if (Array.isArray(ev.preview_image_urls) && ev.preview_image_urls.length) {
              rewritePreviewUrls.value = ev.preview_image_urls
                .filter((x) => typeof x === "string" && x)
                .map((x) => `${x}?t=${Date.now()}`);
            } else if (typeof ev.preview_image_url === "string" && ev.preview_image_url) {
              rewritePreviewUrls.value = [`${ev.preview_image_url}?t=${Date.now()}`];
            }
            const name = typeof ev.template_name === "string" ? ev.template_name : "所选模板";
            templateMsg.value = `已生成预览：${name}。请先检查效果，确认后再下载 ZIP。`;
          }
        });
      } catch (e) {
        stopRewriteCountdown();
        templateErr.value = e instanceof Error ? e.message : "模板改写失败";
      } finally {
        templateBusy.value = false;
      }
    }

    function downloadRewriteBundle() {
      if (!rewriteZipUrl.value) return;
      downloadByUrl(rewriteZipUrl.value);
    }

    const canOptimize = computed(
      () =>
        !!analysisResult.value &&
        confirmOptimize.value &&
        !analyzing.value &&
        !optimizing.value
    );

    const displayOptimized = computed(() => {
      if (optStream.value) return optStream.value;
      return optimized.value;
    });

    loadTemplates();

    return {
      resume,
      jd,
      uploadHint,
      analysisStream,
      analysisResult,
      analysisMeta,
      analysisErr,
      analyzing,
      feedbackAnalysis,
      optStream,
      optimized,
      optErr,
      optimizing,
      optMeta,
      feedbackRefine,
      refineErr,
      refining,
      refineMeta,
      confirmOptimize,
      exportBusy,
      templates,
      templatesLoading,
      templateErr,
      selectedTemplateId,
      templateBusy,
      templateMsg,
      rewriteProgress,
      rewriteProgressText,
      rewritePreviewUrls,
      rewriteZipUrl,
      templateFeedback,
      currentRewriteJobId,
      onSelectTemplate,
      onUpload,
      runAnalyze,
      runReanalyze,
      runOptimize,
      runRefine,
      resetOptimizeUi,
      doExport,
      runTemplateRewrite,
      downloadRewriteBundle,
      canOptimize,
      displayOptimized,
    };
  },
  template: `
    <div>
      <h1>简历优化 Agent</h1>
      <p class="sub">流式分析 / 优化，UTF-8；导出失败时可自动降级为 .txt / .md。</p>
      <section>
        <h2>1. 简历与岗位</h2>
        <label>简历正文（支持粘贴中英文）</label>
        <textarea v-model="resume" placeholder="在此粘贴简历…" spellcheck="false"></textarea>
        <div class="row">
          <label class="inline">
            <span>或上传 PDF / Word / TXT：</span>
            <input type="file" accept=".pdf,.doc,.docx,.txt,.md" @change="onUpload" />
          </label>
        </div>
        <p v-if="uploadHint" class="hint">{{ uploadHint }}</p>
        <label>职位描述 JD</label>
        <textarea v-model="jd" placeholder="粘贴 JD…" spellcheck="false"></textarea>
        <div class="row">
          <button type="button" :disabled="analyzing" @click="runAnalyze">
            {{ analyzing ? '分析中…' : '开始匹配分析（流式）' }}
          </button>
        </div>
        <div v-if="analysisStream" class="stream-box">{{ analysisStream }}</div>
        <div v-if="analysisResult" class="result-box">
          <p>匹配亮点
            <span v-if="analysisMeta.cached" class="badge ok">缓存</span>
            <span v-if="analysisMeta.fallback" class="badge warn">已兜底</span>
          </p>
          <ul><li v-for="(h, i) in analysisResult.highlights" :key="'h'+i">{{ h }}</li></ul>
          <p>主要缺口</p>
          <ul><li v-for="(g, i) in analysisResult.gaps" :key="'g'+i">{{ g }}</li></ul>
          <p>优化建议</p>
          <ul><li v-for="(s, i) in analysisResult.suggestions" :key="'s'+i">{{ s }}</li></ul>
        </div>
        <p v-if="analysisErr" class="err">{{ analysisErr }}</p>
        <template v-if="analysisResult">
          <label>对分析不满意？可填写意见或留空换角度后返修</label>
          <textarea v-model="feedbackAnalysis" placeholder="可选：希望侧重哪些方面…"></textarea>
          <div class="row">
            <button type="button" class="secondary" :disabled="analyzing" @click="runReanalyze">
              {{ analyzing ? '返修中…' : '返修分析' }}
            </button>
          </div>
        </template>
      </section>
      <section v-if="analysisResult">
        <h2>2. 生成优化简历</h2>
        <label class="inline">
          <input type="checkbox" v-model="confirmOptimize" />
          我已确认采纳上述分析要点，请求生成优化简历（将消耗模型额度）
        </label>
        <div class="row">
          <button type="button" :disabled="!canOptimize" @click="runOptimize">
            {{ optimizing ? '生成中（流式）…' : '生成优化简历' }}
          </button>
          <button type="button" class="secondary" :disabled="optimizing" @click="resetOptimizeUi">清空优化稿</button>
        </div>
        <div v-if="displayOptimized" class="stream-box">{{ displayOptimized }}</div>
        <p v-if="optMeta.fallback" class="hint">本次优化已使用服务端非流式兜底完成。</p>
        <p v-if="optErr" class="err">{{ optErr }}</p>
        <template v-if="optimized.trim()">
          <h2 class="mt1">3. 导出</h2>
          <div class="row">
            <button type="button" class="secondary" :disabled="!!exportBusy" @click="doExport('docx')">
              {{ exportBusy === 'docx' ? '…' : 'Word (.docx)' }}
            </button>
            <button type="button" class="secondary" :disabled="!!exportBusy" @click="doExport('pdf')">
              {{ exportBusy === 'pdf' ? '…' : 'PDF' }}
            </button>
            <button type="button" class="secondary" :disabled="!!exportBusy" @click="doExport('txt')">
              {{ exportBusy === 'txt' ? '…' : '纯文本' }}
            </button>
            <button type="button" class="secondary" :disabled="!!exportBusy" @click="doExport('md')">
              {{ exportBusy === 'md' ? '…' : 'Markdown' }}
            </button>
          </div>
          <p class="hint">若 Word/PDF 服务端失败，将自动下载 UTF-8 的 .txt 与 .md。</p>
        </template>
      </section>
      <section v-if="optimized.trim()">
        <h2>4. 对优化稿返修</h2>
        <label>意见（可留空让模型换表述）</label>
        <textarea v-model="feedbackRefine" placeholder="可选：缩短篇幅 / 加强某段 / 更偏技术…"></textarea>
        <div class="row">
          <button type="button" :disabled="refining" @click="runRefine">
            {{ refining ? '返修中…' : '返修优化稿' }}
          </button>
        </div>
        <p v-if="refineMeta.fallback" class="hint">本次返修已使用服务端兜底。</p>
        <p v-if="refineErr" class="err">{{ refineErr }}</p>
      </section>
      <section v-if="optimized.trim()">
        <h2>5. 模板与改写（独立）</h2>
        <p class="hint">从仓库（tex warehouse）选择模板后，会将“优化后的简历 + 模板内容”一起交给大模型生成。生成后先看整份预览图，不满意可反馈重写，最后再下载 ZIP。</p>
        <div class="row" v-if="templatesLoading">
          <span class="hint">模板加载中…</span>
        </div>
        <div class="tpl-grid" v-else>
          <button
            v-for="tpl in templates"
            :key="tpl.id"
            type="button"
            class="tpl-card"
            :class="{ active: selectedTemplateId === tpl.id }"
            @click="onSelectTemplate(tpl.id)"
          >
            <img v-if="tpl.preview_url" :src="tpl.preview_url" :alt="tpl.name" />
            <div v-else class="tpl-noimg">无预览图</div>
            <div class="tpl-name">{{ tpl.name }}</div>
            <div class="tpl-path">{{ tpl.tex_rel_path }}</div>
          </button>
        </div>
        <template v-if="rewritePreviewUrls.length">
          <label class="mt1">对当前模板改写的反馈（用于重写）</label>
          <textarea v-model="templateFeedback" placeholder="例如：教育经历太靠后；技能请更精炼；项目按时间倒序…"></textarea>
        </template>
        <div class="row">
          <button
            type="button"
            :disabled="templateBusy || !selectedTemplateId"
            @click="runTemplateRewrite"
          >
            {{ templateBusy ? '改写中…' : (rewritePreviewUrls.length ? '根据反馈重写预览' : '生成预览') }}
          </button>
          <button
            type="button"
            class="secondary"
            :disabled="!rewriteZipUrl"
            @click="downloadRewriteBundle"
          >
            下载 ZIP
          </button>
        </div>
        <div v-if="templateBusy || rewriteProgressText" class="zip-progress-wrap">
          <div class="zip-progress-label">{{ rewriteProgressText || '准备改写…' }}</div>
          <div class="zip-progress-track">
            <div class="zip-progress-fill" :style="{ width: (rewriteProgress || 0) + '%' }"></div>
          </div>
        </div>
        <div v-if="rewritePreviewUrls.length" class="tpl-preview-wrap">
          <div class="tpl-preview-grid">
            <img
              v-for="(img, idx) in rewritePreviewUrls"
              :key="img + idx"
              :src="img"
              :alt="'改写预览图第' + (idx + 1) + '页'"
              class="tpl-preview-img"
            />
          </div>
        </div>
        <p v-if="templateMsg" class="hint">{{ templateMsg }}</p>
        <p v-if="templateErr" class="err">{{ templateErr }}</p>
      </section>
    </div>
  `,
}).mount("#app");
