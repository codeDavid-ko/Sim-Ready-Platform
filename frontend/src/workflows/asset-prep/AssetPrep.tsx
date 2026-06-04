"use client";

import { useState } from "react";
import { API_BASE, downloadFile } from "@/lib/api";
import { authHeaders } from "@/lib/auth";
import type { WorkflowModuleProps } from "../registry";

type Check = { key: string; ok: boolean; detail: string };
type Asset = { id: string; name: string; filename: string; bytes: number; download_url: string };
type Result = {
  summary: Record<string, unknown>;
  normalization: string[];
  checks: Check[];
  sim_ready: boolean;
  asset: Asset | null;
  registered: boolean;
  usd_preview: string;
};

export default function AssetPrep({ manifest }: WorkflowModuleProps) {
  const accept =
    (manifest.io?.input as { file?: { accept?: string } } | undefined)?.file?.accept ??
    ".glb,.gltf,.obj,.stl,.ply";
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [dlError, setDlError] = useState<string | null>(null);

  async function run(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setResult(null);
    if (!file) {
      setError("3D 파일을 선택하세요.");
      return;
    }
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch(`${API_BASE}/api/workflows/${manifest.id}/run`, {
        method: "POST",
        headers: authHeaders(),
        body: fd,
      });
      if (!res.ok) {
        let d = `오류 (${res.status})`;
        try { d = (await res.json()).detail ?? d; } catch {}
        setError(d);
        return;
      }
      setResult((await res.json()).result as Result);
    } catch {
      setError("백엔드에 연결할 수 없습니다.");
    } finally {
      setBusy(false);
    }
  }

  async function download() {
    if (!result?.asset) return;
    setDlError(null);
    try {
      await downloadFile(result.asset.download_url, result.asset.filename);
    } catch {
      setDlError("다운로드에 실패했습니다.");
    }
  }

  return (
    <>
      <div className="card">
        <p className="muted">{manifest.description}</p>
        <form onSubmit={run}>
          <label>3D 에셋 파일 ({accept})</label>
          <input type="file" accept={accept} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          {error && <p className="err">{error}</p>}
          <div style={{ marginTop: 12 }}>
            <button type="submit" disabled={busy}>{busy ? "변환 중…" : "변환 실행"}</button>
          </div>
        </form>
      </div>

      {result && (
        <>
          <div className="card">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <label style={{ margin: 0 }}>sim-ready 검증</label>
              <span className={`badge ${result.sim_ready ? "ok" : "warn"}`}>
                {result.sim_ready ? "sim-ready ✓" : "검토 필요"}
              </span>
            </div>
            {result.checks.map((c) => (
              <div className="check" key={c.key}>
                <span className="mark">{c.ok ? "✅" : "⚠️"}</span>
                <span><b>{c.key}</b> — {c.detail}</span>
              </div>
            ))}
            {result.normalization.length > 0 && (
              <p className="muted" style={{ marginTop: 10 }}>
                정규화: {result.normalization.join(" · ")}
              </p>
            )}
          </div>

          <div className="card">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <label style={{ margin: 0 }}>결과물</label>
              {result.registered && <span className="badge ok">저장소 등록됨</span>}
            </div>
            {result.asset && (
              <p className="muted">
                {result.asset.filename} · {(result.asset.bytes / 1024).toFixed(1)} KB · id {result.asset.id}
              </p>
            )}
            <div className="row" style={{ marginTop: 8 }}>
              <button className="ghost" onClick={download} disabled={!result.asset}>USD 다운로드</button>
            </div>
            {dlError && <p className="err">{dlError}</p>}
          </div>

          <div className="card">
            <label>USD 미리보기 (앞부분)</label>
            <pre>{result.usd_preview}</pre>
          </div>
        </>
      )}
    </>
  );
}
