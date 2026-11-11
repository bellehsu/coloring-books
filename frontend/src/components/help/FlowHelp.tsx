import styles from "./FlowHelp.module.css";

type Kind = "in" | "comfy" | "be" | "out";
interface Step {
  kind: Kind;
  title: string;
  note?: string[];
}

const KIND_LABEL: Record<Kind, string> = {
  in: "你輸入的",
  comfy: "ComfyUI（GPU）",
  be: "後端程式（不用 GPU）",
  out: "產出",
};

/** 一條流程：方塊之間畫箭頭。or＝這條是前一條的另一種做法。 */
function Flow({ steps, or = false }: { steps: Step[]; or?: boolean }) {
  return (
    <div className={styles.flow}>
      {or && <span className={styles.or}>或</span>}
      {steps.map((s, i) => (
        <div key={s.title + i} className={styles.cell}>
          {i > 0 && (
            <span className={styles.arrow} aria-hidden="true">
              →
            </span>
          )}
          <div className={`${styles.box} ${styles[s.kind]}`}>
            <span className={styles.srOnly}>{KIND_LABEL[s.kind]}：</span>
            <b>{s.title}</b>
            {s.note?.map((n) => (
              <small key={n}>{n}</small>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

const MODELS: { role: string; name: string; file: string; note: string }[] = [
  {
    role: "主模型",
    name: "Qwen-Image-Edit-2511（Q4_K_M 量化）",
    file: "qwen-image-edit-2511-Q4_K_M.gguf",
    note: "unet_gguf・13.2 GB・要裝 ComfyUI-GGUF",
  },
  {
    role: "文字＋看圖",
    name: "Qwen2.5-VL 7B",
    file: "qwen_2.5_vl_7b_fp8_scaled.safetensors",
    note: "text_encoders・讀提示詞，也看角色參考圖",
  },
  { role: "VAE", name: "", file: "qwen_image_vae.safetensors", note: "vae・圖 ↔ latent" },
  {
    role: "加速 LoRA",
    name: "Lightning 4 步",
    file: "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors",
    note: "loras・4 步畫完（約 20–30 秒一張）",
  },
  { role: "去背", name: "BiRefNet", file: "birefnet.safetensors", note: "background_removal・0.44 GB・只有角色動作用" },
];

/** 節點名稱（等寬字）＋中文註解（一般字）。 */
const POSE_NODES: [string, string?][][] = [
  [["UnetLoaderGGUF"], ["ModelSamplingAuraFlow"], ["CFGNorm"], ["LoraLoaderModelOnly"], ["KSampler"]],
  [["LoadImage", "角色參考圖"], ["TextEncodeQwenImageEditPlus"], ["FluxKontextMultiReferenceLatentMethod"], ["KSampler"]],
  [["KSampler"], ["VAEDecode"], ["SaveImage", "彩圖"]],
  [["VAEDecode"], ["RemoveBackground"], ["MaskToImage"], ["SaveImage", "遮罩"]],
];

/** 「現在的畫圖流程」說明：三個 ComfyUI workflow（都是 Qwen-Image-Edit-2511）＋後端的合成、線稿、補線、加字。 */
export function FlowHelp() {
  return (
    <div className={styles.root}>
      <p className={styles.lead}>
        三個 ComfyUI workflow 都用同一顆模型 Qwen-Image-Edit-2511；畫好彩圖之後，合成、線稿、補線、加字都在後端做（不用 AI 模型）。
      </p>
      <ul className={styles.legend} aria-label="顏色說明">
        {(Object.keys(KIND_LABEL) as Kind[]).map((k) => (
          <li key={k}>
            <i className={`${styles.swatch} ${styles[k]}`} aria-hidden="true" />
            {KIND_LABEL[k]}
          </li>
        ))}
      </ul>

      <div className={styles.grid}>
        <div className={styles.main}>
          <section className={styles.lane} aria-labelledby="help-step1">
            <h3 id="help-step1">
              <span className={styles.pill}>一本書一次</span>① 主角參考圖
            </h3>
            <Flow
              steps={[
                { kind: "in", title: "主角描述", note: ["例：穿黃雨衣的小兔子"] },
                { kind: "comfy", title: "qwen_character", note: ["純文字生圖・832×1216"] },
                { kind: "out", title: "角色參考圖", note: ["白底、全身、單一角色"] },
              ]}
            />
            <Flow
              or
              steps={[
                { kind: "in", title: "上傳自己的主角", note: ["手繪、照片都可以"] },
                { kind: "be", title: "縮放、置中貼白底", note: ["不經過 AI、不去背"] },
                { kind: "out", title: "角色參考圖" },
              ]}
            />
          </section>

          <section className={styles.lane} aria-labelledby="help-step2">
            <h3 id="help-step2">
              <span className={styles.pill}>每一頁</span>② 畫背景與角色動作（兩張彩圖）
            </h3>
            <Flow
              steps={[
                { kind: "in", title: "場景", note: ["例：消防局前面"] },
                { kind: "comfy", title: "qwen_background", note: ["純文字生圖・864×1216", "不含角色、東西少而大"] },
                { kind: "out", title: "背景彩圖" },
              ]}
            />
            <Flow
              or
              steps={[
                { kind: "in", title: "上傳自己的背景" },
                { kind: "out", title: "背景彩圖" },
              ]}
            />
            <Flow
              steps={[
                { kind: "in", title: "角色參考圖", note: ["＋ 動作・表情・面向"] },
                {
                  kind: "comfy",
                  title: "qwen_edit_pose_cutout",
                  note: ["拿參考圖「編輯」出新動作（白底）→ BiRefNet 自動去背", "每頁都從同一張參考圖改，所以角色長得一樣"],
                },
                { kind: "out", title: "角色動作圖", note: ["＋ 去背遮罩"] },
              ]}
            />
          </section>

          <section className={styles.lane} aria-labelledby="help-step3">
            <h3 id="help-step3">
              <span className={styles.pill}>每一頁</span>③ 變成著色頁（後端，確定性的影像處理）
            </h3>
            <Flow
              steps={[
                { kind: "be", title: "合成", note: ["Pillow：背景＋去背角色", "位置可以拖"] },
                { kind: "be", title: "擷取線稿", note: ["OpenCV xdog", "彩圖 → 黑白線"] },
                { kind: "be", title: "整理線條", note: ["放大到 A4 300 DPI", "二值化・去碎片・封缺口", "統一線寬 11 px"] },
                { kind: "be", title: "自動補線", note: ["找沒接起來的缺口", "補在 auto 圖層"] },
                { kind: "be", title: "加圖下方的字", note: ["中文／英文／留白"] },
                { kind: "out", title: "可以印", note: ["PNG／整本 PDF"] },
              ]}
            />
          </section>
        </div>

        <aside className={styles.side}>
          <section className={styles.lane} aria-labelledby="help-models">
            <h3 id="help-models">用到的模型（共 5 個檔）</h3>
            <ul className={styles.models}>
              {MODELS.map((m) => (
                <li key={m.file}>
                  <b>{m.role}</b> {m.name}
                  <code>{m.file}</code>
                  <small>{m.note}</small>
                </li>
              ))}
            </ul>
          </section>
          <section className={styles.lane} aria-labelledby="help-nodes">
            <h3 id="help-nodes">qwen_edit_pose_cutout 裡面</h3>
            {POSE_NODES.map((chain, i) => (
              <p key={i} className={styles.chain}>
                {chain.map(([n, note], j) => (
                  <span key={n + j}>
                    {j > 0 && <span aria-hidden="true"> → </span>}
                    <span className={styles.node}>{n}</span>
                    {note && <span className={styles.nodeNote}>（{note}）</span>}
                  </span>
                ))}
              </p>
            ))}
            <p className={styles.fine}>
              另外兩個 workflow 一樣，只是沒有 LoadImage、參考方式、去背節點。固定值：4 步、cfg 1.0、euler／simple、shift 3.1。
            </p>
            <p className={styles.fine}>
              workflow 檔在 <code>comfy_workflows/&lt;名稱&gt;/workflow_api.json</code>，可以直接拖進 ComfyUI 看。
            </p>
          </section>
        </aside>
      </div>
      <p className={styles.closeHint}>按 h 或 Esc 關閉</p>
    </div>
  );
}
