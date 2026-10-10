import { useEffect, useRef, useState } from "react";

// ---------- Tauri 桥接（浏览器 dev 时降级） ----------
const isTauri = "__TAURI_INTERNALS__" in window;

async function invoke(cmd: string, args?: any): Promise<any> {
  if (!isTauri) return { mock: true };
  const { invoke } = (window as any).__TAURI_INTERNALS__;
  return invoke(cmd, args);
}

/** 系统文件选择对话框(Tauri dialog 插件; 浏览器 dev 降级为输入框) */
async function pickFile(title: string): Promise<string | null> {
  if (!isTauri) {
    return window.prompt(`(浏览器dev模式) 手动输入${title}完整路径:`);
  }
  try {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const p = await open({ multiple: false, directory: false, title, filters: [{ name: "Excel", extensions: ["xlsx"] }] });
    return typeof p === "string" ? p : null;
  } catch (e) {
    console.error("文件选择失败", e);
    return null;
  }
}

function useTaskEvents(onEvent: (e: any) => void) {
  const ref = useRef(onEvent);
  ref.current = onEvent;
  useEffect(() => {
    if (!isTauri) return;
    let unlisten: (() => void) | null = null;
    let cancelled = false;
    import("@tauri-apps/api/event")
      .then(({ listen }) => listen<any>("task-event", (ev: any) => ref.current(ev.payload)))
      .then((fn) => { if (cancelled) fn(); else unlisten = fn; })
      .catch((err) => console.error("listen 注册失败", err));
    return () => { cancelled = true; unlisten?.(); };
  }, []);
}

function yesterday() {
  const d = new Date(Date.now() - 86400000);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// ---------- 类型 ----------
type StepState = "running" | "ok" | "failed" | "skipped" | null;
interface Step { id: string; name: string; state: StepState; detail?: string; elapsed_s?: number; error_code?: string }
interface FailCtx {
  platform?: string; store?: string; date?: string; step?: string;
  error_code?: string; title?: string; hint?: string; error?: string;
  shot?: string; attempt?: number; ai_diagnosis?: string; ai_suggestion?: string;
  root_cause?: string; suggested_action?: string; ai_cause?: string; ai_confidence?: string;
}
interface Banner { message: string; action?: string; choices?: string[]; context?: FailCtx; free_text?: boolean }
/** 商家 = 客户端顶层实体; 每平台: 账号密码(登录自动化) + 目标模板表; 关键词在仪表盘单店搜索时用 */
interface Merchant {
  name: string;                       // 商家名(自定义, 如 "茶颜观色")
  taobao?: { username?: string; password?: string; template?: string };
  meituan?: { username?: string; password?: string; template?: string };
  meituan_gj?: { username?: string; password?: string; template?: string };
  jd?: { username?: string; password?: string; template?: string };
  /** 门店明细表(各平台门店名称/唯一编号 → 模板Sheet 精确映射; 可选, 不配=按名称/关键词匹配) */
  store_details?: string;
  /** 已发现的门店列表(全店任务跑完后自动提取, 单店选择的来源) */
  discovered?: Record<string, { id: string; name: string }[]>;
}
interface HistoryItem { date: string; merchant: string; platform: string; ok: boolean; row?: number; filled?: number; ts: string }

const STEPS = [
  { id: "login", name: "登录检查" },
  { id: "download", name: "下载报表" },
  { id: "capture", name: "网页抓取" },
  { id: "weather", name: "天气" },
  { id: "fill", name: "填入模板" },
  { id: "llm", name: "AI 解读" },
];

const ERROR_TEXT: Record<string, string> = {
  E_LOGIN_NEEDED: "请在弹出的浏览器中登录后继续",
  E_CAPTCHA: "检测到滑块/验证码，请手动完成",
  E_TASK_SLOW: "报表生成较慢，可继续等待或取消",
  E_SELECTOR_MISSING: "页面可能已改版，AI 正在分析原因…",
  E_FILE_LOCKED: "模板被 Excel 占用，请关闭后重试",
  E_CHECKSUM_FAIL: "数据校验未通过，请核对",
  E_LOGIN_TIMEOUT: "登录等待超时，请重试",
  E_NO_REPORT: "未找到指定日期的报表文件",
  E_NO_STORE: "未匹配到门店(检查关键词)",
  E_TEMPLATE_MISSING: "模板文件不存在(可能被移动/改名，请重新选择)",
  E_CRASH: "内核异常退出",
};

// 常用服务商预设(base URL 格式各家不同: OpenAI系/v1、火山方舟/api/v3、智谱/paas/v4)
const LLM_PRESETS: { label: string; base: string; model: string; hint: string }[] = [
  { label: "DeepSeek", base: "https://api.deepseek.com/v1", model: "deepseek-chat", hint: "" },
  { label: "火山方舟", base: "https://ark.cn-beijing.volces.com/api/v3", model: "doubao-seed-1-6-250615", hint: "模型 ID 用方舟控制台的完整名称或 ep-xxx 接入点" },
  { label: "智谱 GLM", base: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4.7-flash", hint: "" },
  { label: "SiliconFlow", base: "https://api.siliconflow.cn/v1", model: "deepseek-ai/DeepSeek-V3", hint: "模型用完整 ID 如 deepseek-ai/DeepSeek-V3" },
  { label: "Kimi", base: "https://api.moonshot.cn/v1", model: "moonshot-v1-8k", hint: "" },
  { label: "通义千问", base: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-max", hint: "" },
];

const LS_MERCHANTS = "mrc.merchants";
const LS_LLM = "mrc.llm";
const LS_HIST = "mrc.history";

function loadLS<T>(key: string, def: T): T {
  try { return JSON.parse(localStorage.getItem(key) || "") ?? def; } catch { return def; }
}

const PLATFORM_LABEL: Record<string, string> = { taobao: "淘宝闪购", meituan: "美团", meituan_gj: "美团管家", jd: "京东" };

// ---------- 主应用 ----------
export default function App() {
  const [tab, setTab] = useState<"dashboard" | "history" | "settings">("dashboard");
  const [merchants, setMerchants] = useState<Merchant[]>(() => loadLS(LS_MERCHANTS, [
    { name: "示例商家", taobao: { keyword: "梅溪湖" } },
  ]));
  const [activeMerchant, setActiveMerchant] = useState(merchants[0]?.name ?? "");
  const [activePlatform, setActivePlatform] = useState<"taobao" | "meituan" | "meituan_gj" | "jd">("taobao");
  /** 数据范围: all=全部门店(品牌汇总) / store=单门店(需先跑过全店以发现门店列表) */
  const [scope, setScope] = useState<"all" | "store">("all");
  const [pickedStoreId, setPickedStoreId] = useState("");
  const [dateFrom, setDateFrom] = useState(yesterday());
  const [dateTo, setDateTo] = useState(yesterday());
  /** 用户是否手动改过日期: 改过就尊重其选择, 不再自动对齐昨天 */
  const dateTouched = useRef(false);

  const [running, setRunning] = useState(false);
  // 「一键跑所有平台」的串行状态(用 ref, 事件回调里读到的永远是最新值, 不受闭包快照影响)
  const allQueue = useRef<string[]>([]);      // 还没跑的平台
  const allTotal = useRef(0);                 // 本次一键共几个平台(0 = 非一键模式)
  const allDone = useRef(0);                  // 已跑完几个
  const allResults = useRef<string[]>([]);    // 每个平台的结果文案
  const allCurrent = useRef<string>("");      // 当前正在跑的平台(给 result 回调用)
  const [steps, setSteps] = useState<Record<string, Step>>({});
  const [banner, setBanner] = useState<Banner | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [llmText, setLlmText] = useState("");
  const [result, setResult] = useState<any>(null);
  const [history, setHistory] = useState<HistoryItem[]>(() => loadLS(LS_HIST, []));
  // 报表下载目录默认值: Windows 留空 → 内核自动用 %APPDATA%\MerchantReportClient\reports
  // (原实现硬编码 macOS 路径, 在 Windows 上会被当成 C:\Users\demo\... 建出个莫名其妙的位置)
  const DEFAULT_REPORT_DIR = /Windows/i.test(navigator.userAgent) ? "" : "/Users/demo/Downloads/商家报表整理";
  const [reportDir, setReportDir] = useState(() => loadLS("mrc.reportDir", DEFAULT_REPORT_DIR));
  const logBoxRef = useRef<HTMLDivElement>(null);

  // 日报区间默认「昨天」: 启动 + 每次窗口回到前台 + 跨天(挂托盘过夜)都自动对齐;
  // 用户手动改过(dateTouched)就尊重其选择, 不再动它。
  useEffect(() => {
    const sync = () => {
      if (dateTouched.current) return;
      const y = yesterday();
      setDateFrom((v) => (v === y ? v : y));
      setDateTo((v) => (v === y ? v : y));
    };
    sync();
    const onVis = () => { if (document.visibilityState === "visible") sync(); };
    window.addEventListener("focus", sync);
    document.addEventListener("visibilitychange", onVis);
    const timer = window.setInterval(sync, 60_000);
    return () => {
      window.removeEventListener("focus", sync);
      document.removeEventListener("visibilitychange", onVis);
      window.clearInterval(timer);
    };
  }, []);

  const merchant = merchants.find((m) => m.name === activeMerchant);
  // 该商家当前平台的目标模板(缓存路径); 不存在/已失效由任务时报错兜底
  const templatePath = (merchant as any)?.[activePlatform]?.template ?? "";
  // 已发现的门店列表(全店任务跑完后提取); 单店选择的来源
  const discoveredStores: { id: string; name: string }[] =
    (merchant as any)?.discovered?.[activePlatform] ?? [];
  // 单店模式: 仪表盘内关键词搜索已发现门店(默认用门店选择下拉的关键词过滤)
  const [storeKeyword, setStoreKeyword] = useState("");
  const matchedStores = storeKeyword
    ? discoveredStores.filter((s) => s.name.includes(storeKeyword))
    : discoveredStores;

  useTaskEvents((e) => {
    if (e.type === "task_start") {
      setRunning(true); setSteps({}); setLogs([]); setLlmText(""); setResult(null);
    } else if (e.type === "step") {
      setSteps((s) => ({ ...s, [e.id]: e }));
    } else if (e.type === "log") {
      setLogs((l) => [...l.slice(-300), e.msg]);
    } else if (e.type === "need_human") {
      setBanner({ message: e.message, action: e.action, choices: e.choices,
                  context: e.context, free_text: e.free_text });
    } else if (e.type === "llm_delta") {
      setLlmText((t) => t + e.text);
    } else if (e.type === "result") {
      setResult(e); setRunning(false); setBanner(null);
      if (e.ok) {
        const item: HistoryItem = {
          date: `${scopeLabel}${dateFrom === dateTo ? "" : `(${dateFrom}~${dateTo})`}`,
          merchant: activeMerchant, platform: PLATFORM_LABEL[activePlatform],
          ok: true, row: e.summary?.["写入行"], filled: e.summary?.["写入格数"],
          ts: new Date().toLocaleString("zh-CN"),
        };
        setHistory((h) => { const nh = [item, ...h].slice(0, 100); localStorage.setItem(LS_HIST, JSON.stringify(nh)); return nh; });
        // 全店任务成功 → 事件流里带回发现的门店列表, 更新商家档案
        if (scope === "all" && e.summary?.discovered_stores) {
          const list = e.summary.discovered_stores;
          const ns = merchants.map((m) =>
            m.name === activeMerchant
              ? { ...m, discovered: { ...(m.discovered ?? {}), [activePlatform]: list } }
              : m);
          setMerchants(ns);
          localStorage.setItem(LS_MERCHANTS, JSON.stringify(ns));
        }
      }
      // 一键模式: 记录本平台结果 → 自动接着跑下一个(失败也继续, 最后统一汇总)
      if (allTotal.current > 0 && !e.need_human) {
        allDone.current += 1;
        allResults.current.push(`${PLATFORM_LABEL[allCurrent.current] ?? allCurrent.current}：`
          + (e.ok ? "✅ 成功" : `❌ ${e.error_code || e.message || "失败"}`));
        const next = allQueue.current.shift();
        if (next) {
          window.setTimeout(() => { void launch(next, false); }, 1200);
        } else {
          const okN = allResults.current.filter((s) => s.includes("✅")).length;
          setLogs((l) => [...l,
            "━━━ 一键跑所有平台 · 汇总 ━━━",
            ...allResults.current.map((s) => `  · ${s}`),
            `  共 ${allResults.current.length} 个平台，成功 ${okN} 个。任意平台失败不影响其它平台，可单独重跑该平台。`]);
          allTotal.current = 0; allDone.current = 0;
        }
      }
    } else if (e.type === "task_exit") {
      setRunning(false);
      if (e.stderr_tail?.length) {
        setLogs((l) => [...l, "── 内核诊断输出 ──", ...e.stderr_tail]);
      }
    }
  });

  useEffect(() => { logBoxRef.current?.scrollTo(0, 1e6); }, [logs]);

  async function start(skipBrowser = false) { return launch(activePlatform, skipBrowser); }

  // 单个平台的一次任务(原 start(); 抽出 platform 参数供「一键跑所有平台」串行复用)
  async function launch(platform: string, skipBrowser = false) {
    if (!merchant) return;
    const tpl = (merchant as any)?.[platform]?.template ?? "";
    if (!tpl) {
      setLogs([`❌ 未选择目标模板表。到「设置 → 商家管理」为 ${PLATFORM_LABEL[platform] ?? platform} 选择 xlsx 模板`]);
      return;
    }
    if (scope === "store" && !pickedStoreId) {
      setLogs(discoveredStores.length === 0
        ? ["❌ 尚无可选门店。单门店数据需要先跑一次「全部门店」任务(系统从报表中自动发现门店列表)"]
        : ["❌ 请选择一个门店"]);
      return;
    }
    const picked = discoveredStores.find((s) => s.id === pickedStoreId);
    allCurrent.current = platform;
    if (platform !== activePlatform) setActivePlatform(platform as any);
    setRunning(true); setSteps({}); setLlmText(""); setResult(null);
    setLogs(allTotal.current
      ? [`▶️ 一键模式 第 ${allDone.current + 1}/${allTotal.current} 个平台：${PLATFORM_LABEL[platform] ?? platform}`]
      : []);
    try {
      await invoke("run_task", {
        merchant: merchant.name, platform, date: dateTo,
        dateFrom, dateTo,
        template: tpl,
        keyword: scope === "all" ? "" : (picked?.name ?? storeKeyword),
        storeId: scope === "store" ? pickedStoreId : "",
        scope,
        downloadDir: reportDir,
        storeDetails: (merchant as any)?.store_details ?? "",
        username: (merchant as any)?.[platform]?.username ?? "",
        password: (merchant as any)?.[platform]?.password ?? "",
        skipBrowser,
      });
    } catch (e: any) {
      setLogs((l) => [...l, `启动失败: ${e}`]); setRunning(false);
    }
  }

  // 一键跑所有平台: 依次 淘宝闪购 → 美团 → 美团管家 → 京东(各用各自在设置里配的模板)
  async function startAll() {
    if (!merchant) return;
    const order = ["taobao", "meituan", "meituan_gj", "jd"];
    const missing = order.filter((p) => !(merchant as any)?.[p]?.template);
    if (missing.length) {
      setLogs([`❌ 一键跑所有平台前，请先在「设置 → 商家管理」为这些平台各选一个模板：`
        + missing.map((p) => PLATFORM_LABEL[p] ?? p).join("、")]);
      return;
    }
    allQueue.current = ["meituan", "meituan_gj", "jd"];
    allTotal.current = order.length;
    allDone.current = 0;
    allResults.current = [];
    await launch("taobao", false);
  }

  async function respond(payload: any) { setBanner(null); await invoke("send_to_kernel", { payload }); }
  async function abort() { setBanner(null); setRunning(false); await invoke("abort_task", {}); }

  const stepIcon = (s?: StepState) =>
    s === "ok" ? "✅" : s === "running" ? "⏳" : s === "failed" ? "❌" : s === "skipped" ? "⏭️" : "⬜";

  const scopeLabel = scope === "all" ? "全部门店" : `门店#${pickedStoreId}`;

  return (
    <div className="app">
      <nav className="nav">
        <div className="brand">📊 商家报表助手</div>
        <div className="tabs">
          {([["dashboard", "仪表盘"], ["history", "历史"], ["settings", "设置"]] as const).map(([k, v]) => (
            <button key={k} className={`tab ${tab === k ? "on" : ""}`} onClick={() => setTab(k)}>{v}</button>
          ))}
        </div>
      </nav>

      {banner && banner.context && (
        <div className="failpanel">
          <div className="fail-head">
            <span className="fail-badge">卡住了</span>
            <b>{banner.context.title || "任务遇到问题"}</b>
            {banner.context.error_code && <span className="fail-code">{banner.context.error_code}</span>}
          </div>
          <div className="fail-meta">
            {[banner.context.platform, banner.context.store, banner.context.date, banner.context.step]
              .filter(Boolean).join("  ·  ")}
            {banner.context.attempt ? `  ·  第 ${banner.context.attempt} 次尝试` : ""}
          </div>
          {banner.context.hint && <div className="fail-hint">💡 {banner.context.hint}</div>}
          {banner.context.ai_diagnosis && (
            <div className="fail-ai">
              🤖 AI 定位：{banner.context.ai_cause ? `【${banner.context.ai_cause}】` : ""}
              {banner.context.ai_diagnosis}
              {banner.context.ai_suggestion ? `（依据：${banner.context.ai_suggestion}）` : ""}
              {banner.context.ai_confidence === "low" ? " ⚠️ 证据不足，建议发诊断包" : ""}
            </div>
          )}
          {banner.context.shot && (
            <details className="fail-detail" open>
              <summary>页面现场截图（点开/收起）</summary>
              <img className="fail-shot" src={banner.context.shot} alt="失败现场截图" />
            </details>
          )}
          {banner.context.error && (
            <details className="fail-detail">
              <summary>技术细节（发给开发者时附上）</summary>
              <pre className="fail-pre">{banner.context.error}</pre>
            </details>
          )}
          <div className="fail-actions">
            {banner.choices?.map((c, i) => (
              <button key={c} className={i === 0 ? "" : "ghost"}
                onClick={() => respond({ type: "human_response", action: "confirm", choice: c })}>
                {i === 0 ? `✓ ${c}` : c}
              </button>
            ))}
            <button className="ghost" onClick={abort}>取消任务</button>
          </div>
        </div>
      )}

      {banner && !banner.context && (
        <div className="banner">
          <span>⌨️ {banner.message}</span>
          {banner.action === "login" && <button onClick={() => respond({ type: "human_response", action: "login", result: "done" })}>已完成登录</button>}
          {banner.action === "confirm" && banner.choices?.map((c) => (
            <button key={c} onClick={() => respond({ type: "human_response", action: "confirm", choice: c })}>{c}</button>
          ))}
          <button className="ghost" onClick={abort}>取消任务</button>
        </div>
      )}

      {tab === "dashboard" && (
        <div className="page">
          {/* 商家 + 平台选择 */}
          <div className="panel">
            <div className="row wrap">
              <label>商家</label>
              <select value={activeMerchant} onChange={(e) => setActiveMerchant(e.target.value)}>
                {merchants.map((m) => <option key={m.name}>{m.name}</option>)}
              </select>
              <label>平台</label>
              <div className="seg">
                {Object.entries(PLATFORM_LABEL).map(([k, v]) => (
                  <button key={k} className={`seg-btn ${activePlatform === k ? "on" : ""}`}
                          disabled={!(merchant as any)?.[k]}
                          onClick={() => setActivePlatform(k as any)}
                          title={!(merchant as any)?.[k] ? "该商家未配置此平台" : ""}>{v}</button>
                ))}
              </div>
              <label>范围</label>
              <div className="seg">
                {/* 2026-10-01 用户口径: 去掉「全部门店/单门店」选项 —— 默认就是全部门店,
                    但**逐店抓取**(每家门店单独取自己的数据、写自己的 Sheet), 不再用账号级/品牌汇总口径。 */}
                <span className="dim">全部门店（逐店抓取）</span>
              </div>
            </div>
            <div className="row dim" style={{ marginTop: 6 }}>
              {scope === "all" ? (
                <>逐店抓取：每店单独取数、写入各自的 Sheet · 模板 {templatePath ? templatePath.split("/").pop() : "未选择"}</>
              ) : discoveredStores.length === 0 ? (
                <span className="bad-text">
                  尚无可选门店 — 单门店数据需先跑一次「全部门店」任务，系统会自动从报表中发现门店列表
                </span>
              ) : (
                <>
                  搜索门店
                  <input className="inline-input" placeholder="关键词，如：银盆岭" value={storeKeyword}
                         onChange={(e) => setStoreKeyword(e.target.value)} style={{ width: 140 }} />
                  <select value={pickedStoreId} onChange={(e) => setPickedStoreId(e.target.value)}>
                    <option value="">— 选择 —</option>
                    {matchedStores.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                  </select>
                  （{matchedStores.length} 家）· 模板 {templatePath ? templatePath.split("/").pop() : "未选择"}
                </>
              )}
            </div>
          </div>

          <div className="panel">
            <div className="row wrap">
              <label>报表区间</label>
              <input type="date" value={dateFrom} disabled={running}
                     onChange={(e) => { dateTouched.current = true; setDateFrom(e.target.value); }} />
              <span className="dim">至</span>
              <input type="date" value={dateTo} disabled={running}
                     onChange={(e) => { dateTouched.current = true; setDateTo(e.target.value); }} />
              <button className="ghost" disabled={running} title="把区间重置为昨天"
                      onClick={() => { dateTouched.current = false; const y = yesterday(); setDateFrom(y); setDateTo(y); }}>
                回到昨天
              </button>
              <button className="primary" disabled={running} onClick={() => start(false)}>
                {running ? "运行中…" : "▶ 生成日报"}
              </button>
              <button className="ghost" disabled={running} onClick={startAll}
                      title="依次跑 淘宝闪购 → 美团 → 美团管家 → 京东（各用各自在设置里配的模板；某个平台失败也继续跑下一个）">
                ▶▶ 一键跑所有平台
              </button>
              {running && <button className="ghost" onClick={abort}>终止</button>}
            </div>
            <div className="row dim" style={{ marginTop: 4 }}>
              默认昨天（每次打开/回到前台自动对齐；手动改过就按你选的走）；当前填入区间末日（{dateTo}）的数据
            </div>

            <div className="steps">
              {STEPS.map((s) => {
                const st = steps[s.id];
                return (
                  <div key={s.id} className={`step st-${st?.state ?? "idle"}`}>
                    <span className="icon">{stepIcon(st?.state)}</span>
                    <span>{s.name}</span>
                    {st?.detail && <span className="detail">{st.detail}</span>}
                    {st?.error_code && <span className="err">{ERROR_TEXT[st.error_code] ?? st.error_code}</span>}
                  </div>
                );
              })}
            </div>

            {(llmText || result?.summary?.llm_report) && (
              <div className="llm-box">
                <div className="llm-title">🤖 AI 日报解读</div>
                <div>{llmText || result?.summary?.llm_report}</div>
              </div>
            )}

            {result && (
              <div className={`result ${result.ok ? "ok" : "bad"}`}>
                {result.ok ? "✅ 日报已生成" : `❌ ${result.message || ERROR_TEXT[result.error_code] || "失败"}`}
                {result.summary && (
                  <div className="summary">
                    {Object.entries(result.summary).filter(([k]) => k !== "llm_report").map(([k, v]) => (
                      <span key={k} className="chip">{k}: {String(v)}</span>
                    ))}
                  </div>
                )}
              </div>
            )}

            <div className="logbox" ref={logBoxRef}>
              {logs.map((l, i) => <div key={i}>{l}</div>)}
              {logs.length === 0 && <div className="dim">运行日志将显示在这里</div>}
            </div>
          </div>
        </div>
      )}

      {tab === "history" && (
        <div className="page">
          <div className="panel">
            {history.length === 0 && <div className="dim">暂无任务记录。</div>}
            {history.length > 0 && (
              <table className="hist">
                <thead><tr><th>时间</th><th>商家</th><th>平台</th><th>报表区间</th><th>写入行</th><th>格数</th><th>状态</th></tr></thead>
                <tbody>
                  {history.map((h, i) => (
                    <tr key={i}>
                      <td>{h.ts}</td><td>{h.merchant}</td><td>{h.platform}</td><td>{h.date}</td>
                      <td>{h.row ?? "-"}</td><td>{h.filled ?? "-"}</td>
                      <td className={h.ok ? "ok-text" : "bad-text"}>{h.ok ? "成功" : "失败"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}

      {tab === "settings" && (
        <SettingsPage
          merchants={merchants} setMerchants={setMerchants}
          activeMerchant={activeMerchant} setActiveMerchant={setActiveMerchant}
          onPickFile={pickFile}
          reportDir={reportDir} setReportDir={setReportDir}
        />
      )}
    </div>
  );
}

// ---------- 设置页: 商家管理(关键词+每平台模板表) + LLM ----------
function SettingsPage({ merchants, setMerchants, activeMerchant, setActiveMerchant, onPickFile, reportDir, setReportDir }: {
  merchants: Merchant[]; setMerchants: (m: Merchant[]) => void;
  activeMerchant: string; setActiveMerchant: (s: string) => void;
  onPickFile: (title: string) => Promise<string | null>;
  reportDir: string; setReportDir: (s: string) => void;
}) {
  const [llm, setLlm] = useState(() => loadLS(LS_LLM, { base_url: "", api_key: "", model: "" }));
  const [testMsg, setTestMsg] = useState("");
  const [testOk, setTestOk] = useState<boolean | null>(null);
  const [draft, setDraft] = useState<Merchant>({ name: "" });
  // 模板路径有效性检查(兜底: 文件被移走时提示)
  const [pathCheck, setPathCheck] = useState<Record<string, string>>({});

  function persist(ns: Merchant[]) {
    setMerchants(ns);
    localStorage.setItem(LS_MERCHANTS, JSON.stringify(ns));
  }

  async function pickTemplate(merchantName: string, platform: string) {
    const p = await onPickFile(`选择${PLATFORM_LABEL[platform]}目标模板表`);
    if (!p) return;
    const ns = merchants.map((m) =>
      m.name === merchantName
        ? { ...m, [platform]: { ...(m as any)[platform], template: p } }
        : m);
    persist(ns);
    setPathCheck((c) => ({ ...c, [`${merchantName}.${platform}`]: "" }));
  }

  async function pickStoreDetails(merchantName: string) {
    const p = await onPickFile("选择门店明细表（各平台门店名称/唯一编号）");
    if (!p) return;
    persist(merchants.map((m) => (m.name === merchantName ? { ...m, store_details: p } : m)));
  }

  async function verifyTemplate(merchantName: string, platform: string, path?: string) {
    if (!path) { setPathCheck((c) => ({ ...c, [`${merchantName}.${platform}`]: "未选择" })); return; }
    // Tauri 环境由内核/壳校验存在性; 这里先走 fetch not allowed, 用 invoke
    try {
      const r = await invoke("check_file", { path });
      setPathCheck((c) => ({
        ...c,
        [`${merchantName}.${platform}`]: r?.exists ? "✓ 文件有效" : "✗ 文件不存在(被移动或改名?)",
      }));
    } catch {
      setPathCheck((c) => ({ ...c, [`${merchantName}.${platform}`]: "(校验需在客户端内进行)" }));
    }
  }

  function addMerchant() {
    const name = draft.name.trim();
    if (!name || merchants.some((m) => m.name === name)) return;
    const ns = [...merchants, { name }];
    persist(ns);
    setActiveMerchant(name);
    setDraft({ name: "" });
  }

  function removeMerchant(name: string) {
    const ns = merchants.filter((m) => m.name !== name);
    persist(ns);
    if (activeMerchant === name) setActiveMerchant(ns[0]?.name ?? "");
  }

  async function saveLlm() {
    localStorage.setItem(LS_LLM, JSON.stringify(llm));
    setTestOk(null); setTestMsg("已保存（本地）");
  }

  async function testLlm() {
    setTestMsg("测试中…"); setTestOk(null);
    // Tauri HTTP 插件: 请求由 Rust 层发出, 无 CORS 限制; 浏览器 dev 才走 fetch
    const url = llm.base_url.replace(/\/$/, "") + "/chat/completions";
    const body = JSON.stringify({ model: llm.model, messages: [{ role: "user", content: "请只回复：连接成功" }], max_tokens: 20 });
    const headers = { "Content-Type": "application/json", Authorization: `Bearer ${llm.api_key}` };
    try {
      let status = 0;
      let text = "";
      let errBody = "";
      if (isTauri) {
        const { fetch: tauriFetch } = await import("@tauri-apps/plugin-http");
        const resp = await tauriFetch(url, { method: "POST", headers, body, connectTimeout: 15000 });
        status = resp.status;
        const raw = await resp.text();
        try {
          const j = JSON.parse(raw);
          if (status === 200) text = j?.choices?.[0]?.message?.content ?? "";
          else errBody = j?.error?.message || j?.message || raw.slice(0, 120);
        } catch { errBody = raw.slice(0, 120); }
      } else {
        const resp = await fetch(url, { method: "POST", headers, body });
        status = resp.status;
      }
      if (status === 200) { setTestOk(true); setTestMsg(`✅ 连接成功${text ? `："${text.slice(0, 20)}"` : ""}`); }
      else if (status === 401) { setTestOk(false); setTestMsg(`❌ API Key 无效（401）${errBody ? ` · ${errBody}` : ""}`); }
      else if (status === 404) { setTestOk(false); setTestMsg(`❌ 模型或路径不存在（404）${errBody ? ` · ${errBody}` : ""}`); }
      else { setTestOk(false); setTestMsg(`❌ HTTP ${status}${errBody ? ` · ${errBody}` : ""}`); }
    } catch (e: any) {
      setTestOk(false);
      setTestMsg(`❌ 无法连接: ${String(e).slice(0, 80)}`);
    }
  }

  const m = merchants.find((x) => x.name === activeMerchant);

  return (
    <div className="page">
      <div className="panel">
        <h3 className="sect">🏬 商家管理</h3>
        <div className="row wrap" style={{ marginBottom: 10 }}>
          <label>当前商家</label>
          <select value={activeMerchant} onChange={(e) => setActiveMerchant(e.target.value)}>
            {merchants.map((x) => <option key={x.name}>{x.name}</option>)}
          </select>
          <button className="ghost" onClick={() => removeMerchant(activeMerchant)} disabled={merchants.length <= 1}>删除</button>
        </div>

        {m && (
          <div className="platform-cards">
            {(["taobao", "meituan", "meituan_gj", "jd"] as const).map((pf) => {
              const cfg = (m as any)[pf] as { username?: string; password?: string; template?: string } | undefined;
              const key = `${m.name}.${pf}`;
              return (
                <div key={pf} className="plat-card">
                  <div className="plat-title">{PLATFORM_LABEL[pf]}</div>
                  <div className="form">
                    <label>账号（自动填充登录）</label>
                    <input placeholder="手机号/账号" value={cfg?.username ?? ""}
                           onChange={(e) => persist(merchants.map((x) =>
                             x.name === m.name ? { ...x, [pf]: { ...(x as any)[pf], username: e.target.value } } : x))} />
                    <label>密码（用于自动填充；滑块仍需人工）</label>
                    <input type="password" placeholder="登录密码" value={cfg?.password ?? ""}
                           onChange={(e) => persist(merchants.map((x) =>
                             x.name === m.name ? { ...x, [pf]: { ...(x as any)[pf], password: e.target.value } } : x))} />
                    <label>目标模板表（xlsx，选一次自动缓存）</label>
                    <div className="row">
                      <button className="ghost" onClick={() => pickTemplate(m.name, pf)}>
                        {cfg?.template ? "重新选择" : "选择文件…"}
                      </button>
                      <span className="path" title={cfg?.template}>{cfg?.template || "未选择"}</span>
                    </div>
                    {cfg?.template && (
                      <div className="row">
                        <button className="link" onClick={() => verifyTemplate(m.name, pf, cfg.template)}>检查文件有效性</button>
                        <span className={pathCheck[key]?.startsWith("✓") ? "ok-text" : "bad-text"}>{pathCheck[key]}</span>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {m && (
          <div className="form" style={{ marginTop: 10, borderTop: "1px solid var(--border)", paddingTop: 10 }}>
            <label>门店明细表（各平台「门店名称 + 唯一编号」→ 模板 Sheet 的精确映射；可选，选一次自动缓存）</label>
            <div className="row">
              <button className="ghost" onClick={() => pickStoreDetails(m.name)}>
                {(m as any).store_details ? "重新选择" : "选择文件…"}
              </button>
              <span className="path" title={(m as any).store_details}>{(m as any).store_details || "未选择（不配置=按名称/关键词匹配，行为同旧版）"}</span>
              {(m as any).store_details && (
                <button className="link" onClick={() => persist(merchants.map((x) => (x.name === m.name ? { ...x, store_details: "" } : x)))}>清除</button>
              )}
            </div>
          </div>
        )}

        <div className="new-merchant row">
          <input placeholder="新商家名称（自定义，如：示例商家）" value={draft.name}
                 onChange={(e) => setDraft({ name: e.target.value })} />
          <button className="primary" onClick={addMerchant}>＋ 添加商家</button>
        </div>
      </div>

      <div className="panel">
        <h3 className="sect">📂 报表目录</h3>
        <div className="form">
          <label>报表下载/查找目录（商家后台下载的报表也放这里）</label>
          <div className="row">
            <span className="path" style={{ maxWidth: 420, fontSize: 13 }} title={reportDir}>{reportDir}</span>
          </div>
          <div className="row">
            <button className="ghost" onClick={async () => {
              if (!isTauri) { const p = window.prompt("(浏览器dev) 输入报表目录路径:"); if (p) { setReportDir(p); localStorage.setItem("mrc.reportDir", p); } return; }
              try {
                const { open } = await import("@tauri-apps/plugin-dialog");
                const p = await open({ directory: true, multiple: false, title: "选择报表目录" });
                if (typeof p === "string") { setReportDir(p); localStorage.setItem("mrc.reportDir", p); }
              } catch (e) { console.error(e); }
            }}>选择目录…</button>
            <span className="dim">平台报表会下载到这里，填表也从这里找报表</span>
          </div>
        </div>
      </div>

      <div className="panel">
        <h3 className="sect">🤖 大模型配置</h3>
        <div className="form">
          <label>快速选择服务商（自动填 Base URL 和示例模型，可再改）</label>
          <select onChange={(e) => {
            const p = LLM_PRESETS[+e.target.value];
            if (p) { setLlm({ ...llm, base_url: p.base, model: p.model }); setTestOk(null); setTestMsg(p.hint); }
          }}>
            <option value="">— 选择服务商 —</option>
            {LLM_PRESETS.map((p, i) => <option key={p.label} value={i}>{p.label} · {p.base}</option>)}
          </select>
          <label>Base URL</label>
          <input placeholder="https://api.deepseek.com/v1" value={llm.base_url}
                 onChange={(e) => setLlm({ ...llm, base_url: e.target.value })} />
          <label>API Key</label>
          <input type="password" placeholder="sk-..." value={llm.api_key}
                 onChange={(e) => setLlm({ ...llm, api_key: e.target.value })} />
          <label>模型 ID</label>
          <input placeholder="deepseek-chat / glm-4.7-flash / doubao-seed-1-6-250615" value={llm.model}
                 onChange={(e) => setLlm({ ...llm, model: e.target.value })} />
          <div className="row" style={{ marginTop: 10 }}>
            <button className="primary" onClick={saveLlm}>保存</button>
            <button className="ghost" onClick={testLlm}>测试连接</button>
            {testMsg && <span className={testOk === true ? "ok-text" : testOk === false ? "bad-text" : "dim"}>{testMsg}</span>}
          </div>
        </div>
      </div>
    </div>
  );
}
