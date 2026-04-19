/**
 * UTF-8 SSE consumer for POST endpoints (DeepSeek-style data: lines).
 */

export type SseHandler = (event: Record<string, unknown>) => void;

export async function postSse(
  url: string,
  body: unknown,
  onEvent: SseHandler
): Promise<void> {
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
      const j = (await res.json()) as { detail?: unknown };
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

  while (true) {
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
            onEvent(JSON.parse(json) as Record<string, unknown>);
          } catch {
            /* ignore malformed chunk */
          }
        }
      }
    }
  }
}

export function downloadUtf8Blob(filename: string, text: string, mime: string) {
  const blob = new Blob([text], { type: `${mime}; charset=utf-8` });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

export async function exportResumeFile(
  format: "docx" | "pdf" | "txt" | "md",
  content: string
): Promise<{ ok: true } | { ok: false; fallback: boolean }> {
  const res = await fetch("/api/export-resume-file", {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify({ content, format }),
  });

  const ct = res.headers.get("content-type") || "";

  if (ct.includes("application/json")) {
    const j = (await res.json()) as {
      detail?: string;
      fallback?: string[];
      code?: string;
    };
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
