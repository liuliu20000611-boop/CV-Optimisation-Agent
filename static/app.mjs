import { computed, createApp, ref } from "vue";
import { exportResumeFile, postSse } from "./sse.mjs";

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
      onUpload,
      runAnalyze,
      runReanalyze,
      runOptimize,
      runRefine,
      resetOptimizeUi,
      doExport,
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
          <h2 class="mt1">4. 对优化稿返修</h2>
          <label>意见（可留空让模型换表述）</label>
          <textarea v-model="feedbackRefine" placeholder="可选：缩短篇幅 / 加强某段 / 更偏技术…"></textarea>
          <div class="row">
            <button type="button" :disabled="refining" @click="runRefine">
              {{ refining ? '返修中…' : '返修优化稿' }}
            </button>
          </div>
          <p v-if="refineMeta.fallback" class="hint">本次返修已使用服务端兜底。</p>
          <p v-if="refineErr" class="err">{{ refineErr }}</p>
        </template>
      </section>
    </div>
  `,
}).mount("#app");
