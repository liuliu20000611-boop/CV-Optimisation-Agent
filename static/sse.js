/**
 * UTF-8 SSE + 导出（与 frontend/src/api/sse.ts 行为一致）
 */

export async function postSse(url, body, onEvent) {
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      Accept: "text/event-stream; charset=utf-8",
    },
    body: JSON.stringify(body),
  });

  const ct = res.headers.get("content-type") || "";
  if (!res.ok && !ct.includes("text/event-stream")) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      if (typeof j.detail === "string") msg = j.detail;
      else if (Array.isArray(j.detail)) msg = JSON.stringify(j.detail);
    } catch {
      const t = await res.text();
      if (t) msg = t;
    }
    throw new Error(msg);
  }

  const reader = res.body?.getReader();
  if (!reader) throw new Error("无响应流");

  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() ?? "";
    for (const block of blocks) {
      for (const line of block.split("\n")) {
        const trimmed = line.trim();
        if (trimmed.startsWith("data: ")) {
          const json = trimmed.slice(6);
          try {
            onEvent(JSON.parse(json));
          } catch {
            /* ignore */
          }
        }
      }
    }
  }
}

export function downloadUtf8Blob(filename, text, mime) {
  const blob = new Blob([text], { type: `${mime}; charset=utf-8` });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

export async function exportResumeFile(format, content) {
  const res = await fetch("/api/export-resume-file", {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify({ content, format }),
  });

  const ct = res.headers.get("content-type") || "";

  if (ct.includes("application/json")) {
    const j = await res.json();
    if (j.fallback?.length) {
      downloadUtf8Blob("optimized-resume.txt", content, "text/plain");
      downloadUtf8Blob("optimized-resume.md", content, "text/markdown");
      return { ok: false, fallback: true };
    }
    throw new Error(j.detail || "导出失败");
  }

  if (!res.ok) {
    throw new Error(await res.text().catch(() => res.statusText));
  }

  const blob = await res.blob();
  const dispo = res.headers.get("Content-Disposition") || "";
  let fname =
    format === "docx"
      ? "optimized-resume.docx"
      : format === "pdf"
        ? "optimized-resume.pdf"
        : format === "md"
          ? "optimized-resume.md"
          : "optimized-resume.txt";
  const star = /filename\*=UTF-8''([^;\s]+)/i.exec(dispo);
  const plain = /filename="([^"]+)"/i.exec(dispo);
  try {
    if (star?.[1]) fname = decodeURIComponent(star[1]);
    else if (plain?.[1]) fname = plain[1];
  } catch {
    /* keep */
  }
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = fname;
  a.click();
  URL.revokeObjectURL(a.href);
  return { ok: true };
}

export async function fetchTexTemplates() {
  const res = await fetch("/api/tex-templates");
  if (!res.ok) {
    throw new Error(await res.text().catch(() => "获取模板失败"));
  }
  return await res.json();
}

function parseFilenameFromDisposition(dispo, fallback) {
  const star = /filename\*=UTF-8''([^;\s]+)/i.exec(dispo || "");
  const plain = /filename="([^"]+)"/i.exec(dispo || "");
  try {
    if (star?.[1]) return decodeURIComponent(star[1]);
    if (plain?.[1]) return plain[1];
  } catch {
    /* ignore malformed header */
  }
  return fallback;
}

export async function rewriteWithTemplate(templateId, optimizedResume) {
  const res = await fetch("/api/template-rewrite", {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify({
      template_id: templateId,
      optimized_resume: optimizedResume,
    }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || "模板改写失败");
  }
  if (typeof data.zip_download_url !== "string") {
    throw new Error("模板改写返回格式异常");
  }
  return data;
}

export function downloadByUrl(url) {
  const a = document.createElement("a");
  a.href = url;
  a.download = "";
  a.click();
}

export async function downloadFileWithProgress(url, onProgress) {
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(await res.text().catch(() => "下载失败"));
  }
  const total = Number(res.headers.get("content-length") || 0);
  const reader = res.body?.getReader();
  if (!reader) throw new Error("下载流不可用");

  const chunks = [];
  let loaded = 0;
  onProgress?.({ loaded, total, percent: total > 0 ? 0 : null });
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    if (value) {
      chunks.push(value);
      loaded += value.byteLength;
      onProgress?.({
        loaded,
        total,
        percent: total > 0 ? Math.min(100, Math.round((loaded / total) * 100)) : null,
      });
    }
  }

  const blob = new Blob(chunks, { type: res.headers.get("content-type") || "application/octet-stream" });
  const filename = parseFilenameFromDisposition(
    res.headers.get("content-disposition") || "",
    "rendered-resume-bundle.zip"
  );
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
  onProgress?.({ loaded, total, percent: 100 });
}
