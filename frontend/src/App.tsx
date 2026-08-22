import '@fontsource-variable/noto-sans-kr';
import {
  ArrowsClockwise,
  CaretDown,
  CheckCircle,
  CircleNotch,
  CloudRain,
  Code,
  Database,
  PaperPlaneTilt,
  ShieldWarning,
  Sun,
} from '@phosphor-icons/react';
import { FormEvent, KeyboardEvent, useEffect, useMemo, useState } from 'react';

const KNOWN_EVENTS = new Set([
  'session.start',
  'assistant.status',
  'tool.call',
  'tool.result',
  'assistant.final',
  'session.error',
  'session.done',
]);

type Evidence = {
  product_id?: string;
  source?: string;
  freshness?: string;
  row_count?: number;
  sample_only?: boolean;
  rows?: unknown[];
  [key: string]: unknown;
};

type AgentPayload = {
  trace_id?: string;
  provider_mode?: string;
  status?: string;
  tool_name?: string;
  input?: unknown;
  output?: unknown;
  evidence?: Evidence[];
  envelope?: {
    answer?: string;
    evidence?: Evidence[];
    evidence_status?: string;
    [key: string]: unknown;
  };
  error?: string | { code?: string; message?: string; retryable?: boolean };
  message?: string;
  [key: string]: unknown;
};

type TimelineEvent = {
  id: number;
  name: string;
  data: AgentPayload;
};

type ParsedSseEvent = {
  event: string;
  data: string;
};

type WeatherProduct = {
  label: string;
  productId: string;
  question: string;
  description: string;
  icon: 'current' | 'change' | 'precipitation' | 'risk';
};

type ProviderMode = 'demo' | 'gemini' | 'anthropic';

type MetaState = 'loading' | 'ready' | 'not-ready' | 'degraded';

type ServerMeta = {
  providerMode: ProviderMode;
  ready: boolean;
  supportedProductIds: string[];
};

const WEATHER_PRODUCTS: readonly WeatherProduct[] = [
  {
    label: '지금 날씨',
    productId: 'weather_place_current_outlook',
    question: '지금 서울 주요 장소들의 기상 상태는 대체로 어떻습니까?',
    description: '현재 상태와 요약',
    icon: 'current',
  },
  {
    label: '예보 변화',
    productId: 'weather_place_forecast_change_daily',
    question: '서울 주요 장소의 오늘 예보는 직전 KMA 발표보다 어떻게 달라졌습니까?',
    description: '직전 발표와 비교',
    icon: 'change',
  },
  {
    label: '비·눈 시간대',
    productId: 'weather_place_precipitation_window',
    question: '서울 주요 장소 중 비나 눈이 연속으로 예보된 시간대는 언제입니까?',
    description: '강수 시작·종료 시각',
    icon: 'precipitation',
  },
  {
    label: '위험 시간대',
    productId: 'weather_place_risk_window',
    question: '서울 주요 장소 중 방문·이동 주의가 필요할 수 있는 예보 시간과 근거는 무엇입니까?',
    description: '호우·폭염 등 위험 구간',
    icon: 'risk',
  },
];

const PRODUCT_TITLES: Record<string, string> = {
  weather_place_current_outlook: '장소별 현재 날씨 개황',
  weather_place_forecast_change_daily: '장소별 예보 변화',
  weather_place_precipitation_window: '장소별 강수 예상 시간대',
  weather_place_risk_window: '장소별 기상 위험 예상 시간대',
};

const WEATHER_PRODUCTS_BY_ID = new Map(
  WEATHER_PRODUCTS.map((product) => [product.productId, product]),
);

const STATUS_LABELS: Record<string, string> = {
  started: '시작됨',
  requesting_model_turn: '답변을 준비하고 있어요',
  completed: '완료',
  completed_with_error: '오류와 함께 완료',
  error: '오류',
  ok: '정상',
};

const EVENT_LABELS: Record<string, string> = {
  'session.start': '세션 시작',
  'assistant.status': '응답 준비',
  'tool.call': '도구 호출',
  'tool.result': '도구 결과',
  'assistant.final': '최종 답변',
  'session.error': '세션 오류',
  'session.done': '세션 완료',
};

const TOOL_LABELS: Record<string, string> = {
  search_products: '제품 검색',
  preview_product: '기상 근거 조회',
};

function WeatherIcon({ kind, size = 24 }: { kind: WeatherProduct['icon']; size?: number }) {
  const iconProps = { size, weight: 'duotone' as const, 'aria-hidden': true };
  if (kind === 'change') return <ArrowsClockwise {...iconProps} />;
  if (kind === 'precipitation') return <CloudRain {...iconProps} />;
  if (kind === 'risk') return <ShieldWarning {...iconProps} />;
  return <Sun {...iconProps} />;
}

function parseServerMeta(value: unknown): ServerMeta {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('invalid meta payload');
  }
  const meta = value as Record<string, unknown>;
  if (!['demo', 'gemini', 'anthropic'].includes(String(meta.provider_mode))) {
    throw new Error('invalid provider mode');
  }
  if (typeof meta.ready !== 'boolean' || !Array.isArray(meta.supported_products)) {
    throw new Error('invalid meta contract');
  }

  const productIds: string[] = [];
  for (const valueProduct of meta.supported_products) {
    if (!valueProduct || typeof valueProduct !== 'object' || Array.isArray(valueProduct)) {
      throw new Error('invalid supported product');
    }
    const productId = (valueProduct as Record<string, unknown>).product_id;
    if (typeof productId !== 'string' || !productId) {
      throw new Error('invalid supported product id');
    }
    if (WEATHER_PRODUCTS_BY_ID.has(productId) && !productIds.includes(productId)) {
      productIds.push(productId);
    }
  }
  if (meta.ready && productIds.length === 0) {
    throw new Error('no supported weather products');
  }

  return {
    providerMode: meta.provider_mode as ProviderMode,
    ready: meta.ready,
    supportedProductIds: productIds,
  };
}

function parseSseBlock(block: string): ParsedSseEvent | null {
  let event = 'message';
  const dataLines: string[] = [];

  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) {
      event = line.slice('event:'.length).trim();
      continue;
    }
    if (line.startsWith('data:')) {
      dataLines.push(line.slice('data:'.length).trimStart());
    }
  }

  return dataLines.length ? { event, data: dataLines.join('\n') } : null;
}

async function readAgentStream(
  response: Response,
  onEvent: (eventName: string, payload: AgentPayload) => void,
): Promise<void> {
  if (!response.ok) {
    throw new Error(`요청에 실패했습니다. (HTTP ${response.status})`);
  }
  if (!response.body) {
    throw new Error('서버 응답에 스트림 데이터가 없습니다.');
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const blocks = buffer.split('\n\n');
    buffer = blocks.pop() ?? '';

    for (const block of blocks) {
      const parsed = parseSseBlock(block.trimEnd());
      if (!parsed || !KNOWN_EVENTS.has(parsed.event)) continue;
      try {
        onEvent(parsed.event, JSON.parse(parsed.data) as AgentPayload);
      } catch {
        throw new Error('스트림 데이터 형식이 올바르지 않습니다.');
      }
    }
    if (done) break;
  }

  const tail = parseSseBlock(buffer.trim());
  if (tail && KNOWN_EVENTS.has(tail.event)) {
    try {
      onEvent(tail.event, JSON.parse(tail.data) as AgentPayload);
    } catch {
      throw new Error('스트림 데이터 형식이 올바르지 않습니다.');
    }
  }
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function present(value: unknown, fallback = '—'): string {
  return value === null || value === undefined || value === '' ? fallback : String(value);
}

function metric(value: unknown, unit: string): string {
  return value === null || value === undefined || value === '' ? '—' : `${String(value)}${unit}`;
}

function metricPair(previous: unknown, latest: unknown, unit: string): string {
  return `${metric(previous, unit)} → ${metric(latest, unit)}`;
}

function compactTime(value: unknown): string {
  const text = present(value);
  return text === '—' ? text : text.replace('T', ' ').slice(0, 16);
}

function placeCell(row: Record<string, unknown>) {
  return (
    <span className="place-cell">
      <strong>{present(row.place_name ?? row.admin_dong ?? row.place)}</strong>
      {row.gu ? <small>{present(row.gu)}</small> : null}
    </span>
  );
}

type Column = {
  label: string;
  render: (row: Record<string, unknown>) => React.ReactNode;
};

function evidenceColumns(productId: string | undefined): Column[] {
  if (productId === 'weather_place_forecast_change_daily') {
    return [
      { label: '장소', render: placeCell },
      { label: '예보일', render: (row) => present(row.forecast_date) },
      {
        label: '변화',
        render: (row) => ({ unchanged: '변화 없음', changed: '변경됨', new: '새 예보' }[present(row.change_state)] ?? present(row.change_state)),
      },
      { label: '최저기온', render: (row) => metricPair(row.previous_min_temp_c, row.latest_min_temp_c, '℃') },
      { label: '최고기온', render: (row) => metricPair(row.previous_max_temp_c, row.latest_max_temp_c, '℃') },
      { label: '최대 강수확률', render: (row) => metricPair(row.previous_max_precip_prob_pct, row.latest_max_precip_prob_pct, '%') },
    ];
  }
  if (productId === 'weather_place_precipitation_window') {
    return [
      { label: '장소', render: placeCell },
      { label: '시작', render: (row) => compactTime(row.window_start_at) },
      { label: '종료', render: (row) => compactTime(row.window_end_at) },
      { label: '지속', render: (row) => metric(row.precipitation_hour_count, '시간') },
      { label: '최대 강수확률', render: (row) => metric(row.precip_prob_max_pct, '%') },
    ];
  }
  if (productId === 'weather_place_risk_window') {
    return [
      { label: '장소', render: placeCell },
      { label: '예보 시각', render: (row) => compactTime(row.forecast_at) },
      { label: '위험 근거', render: (row) => <strong className="risk-label">{present(row.risk_labels ?? row.risk)}</strong> },
      { label: '기온', render: (row) => metric(row.temp_c, '℃') },
      { label: '강수확률', render: (row) => metric(row.precip_prob_pct, '%') },
      { label: '풍속', render: (row) => metric(row.wind_ms, 'm/s') },
    ];
  }
  return [
    { label: '장소', render: placeCell },
    { label: '예보 시각', render: (row) => compactTime(row.forecast_at) },
    {
      label: '날씨',
      render: (row) => [present(row.sky_label, ''), present(row.pty_label, '')].filter(Boolean).join(' · ') || '—',
    },
    { label: '기온', render: (row) => metric(row.temp_c, '℃') },
    { label: '습도', render: (row) => metric(row.humidity_pct, '%') },
    { label: '강수확률', render: (row) => metric(row.precip_prob_pct, '%') },
  ];
}

function stringifyBounded(value: unknown): string {
  const text = JSON.stringify(value, null, 2) ?? '';
  return text.length > 4800 ? `${text.slice(0, 4800)}\n... 이하 생략` : text;
}

function splitAnswerNotice(value: string): { answer: string; notice: string } {
  const paragraphs = value.trim().split(/\n\s*\n/);
  const firstParagraph = paragraphs[0]?.trim() ?? '';
  if (/^주의\s*:/.test(firstParagraph) && paragraphs.length > 1) {
    return {
      notice: firstParagraph.replace(/^주의\s*:\s*/, ''),
      answer: paragraphs.slice(1).join('\n\n').trim(),
    };
  }
  return { answer: value.trim(), notice: '' };
}

function EvidenceTable({ evidence }: { evidence: Evidence }) {
  const [showAllRows, setShowAllRows] = useState(false);
  const rows = Array.isArray(evidence.rows) ? evidence.rows.map(asRecord) : [];
  const columns = evidenceColumns(evidence.product_id);
  const visibleRows = showAllRows ? rows : rows.slice(0, 3);
  const additionalRowCount = Math.max(0, rows.length - 3);

  if (!rows.length) {
    return <p className="empty-state">표시할 미리보기 행이 없습니다.</p>;
  }

  return (
    <>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>{columns.map((column) => <th key={column.label} scope="col">{column.label}</th>)}</tr>
          </thead>
          <tbody>
            {visibleRows.map((row, rowIndex) => (
              <tr key={present(row.product_row_id, String(rowIndex))}>
                {columns.map((column) => (
                  <td key={column.label} data-label={column.label}>{column.render(row)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {additionalRowCount > 0 ? (
        <button
          type="button"
          className="rows-toggle"
          aria-expanded={showAllRows}
          onClick={() => setShowAllRows((current) => !current)}
        >
          {showAllRows ? `${additionalRowCount}행 접기` : `${additionalRowCount}행 더 보기`}
        </button>
      ) : null}
    </>
  );
}

function EvidenceProduct({ evidence, initiallyOpen }: { evidence: Evidence; initiallyOpen: boolean }) {
  const [open, setOpen] = useState(initiallyOpen);
  const [rawOpen, setRawOpen] = useState(false);
  const title = PRODUCT_TITLES[evidence.product_id ?? ''] ?? '기상 데이터 근거';
  const rowCount = typeof evidence.row_count === 'number'
    ? evidence.row_count
    : Array.isArray(evidence.rows) ? evidence.rows.length : 0;

  return (
    <section className="evidence-product">
      <button
        type="button"
        className="evidence-summary"
        aria-expanded={open}
        aria-label={`${title} ${open ? '접기' : '펼치기'}`}
        onClick={() => setOpen((current) => !current)}
      >
        <span className="evidence-title">
          <Database size={20} weight="duotone" aria-hidden />
          <strong>{title}</strong>
          <span>최신 {present(evidence.freshness, '기준 시각 미제공')} 기준</span>
        </span>
        <span className="evidence-count">
          {rowCount}행 샘플
          <CaretDown size={18} weight="bold" aria-hidden />
        </span>
      </button>

      <div className="evidence-body" hidden={!open}>
        <EvidenceTable evidence={evidence} />
        <div className="evidence-meta">
          <span>출처: {evidence.source === 'ASK Seoul preview' ? 'ASK Seoul 공개 API' : present(evidence.source)}</span>
          <span>공개 미리보기 최대 5행</span>
        </div>
        <button
          type="button"
          className="raw-toggle"
          aria-expanded={rawOpen}
          onClick={() => setRawOpen((current) => !current)}
        >
          <Code size={17} weight="bold" aria-hidden />
          {rawOpen ? '원본 JSON 접기' : '원본 JSON 보기'}
        </button>
        <div className="raw-json" hidden={!rawOpen}>
          <pre>{stringifyBounded(evidence.rows ?? evidence)}</pre>
        </div>
      </div>
    </section>
  );
}

function formatStatus(status: string): string {
  return STATUS_LABELS[status] ?? (/[가-힣]/.test(status) ? status : '에이전트 실행 중');
}

function formatProviderMode(mode: string): string {
  const labels: Record<string, string> = {
    'not connected': '연결 대기',
    connecting: '연결 중',
    demo: '데모 모드',
    gemini: 'LIVE · Gemini',
    anthropic: 'LIVE · Anthropic',
  };
  return labels[mode] ?? mode;
}

function formatTimelineDetail(payload: AgentPayload): string {
  if (payload.tool_name) return TOOL_LABELS[payload.tool_name] ?? '허용된 기상 도구';
  if (payload.status) return formatStatus(payload.status);
  return '수신됨';
}

function sessionErrorMessage(payload: AgentPayload): string {
  const structuredError = payload.error;
  const rawMessage = typeof structuredError === 'string'
    ? structuredError
    : structuredError?.message ?? payload.message;

  if (rawMessage && /[가-힣]/.test(rawMessage)) return rawMessage;
  if (typeof structuredError !== 'string' && structuredError?.code === 'upstream_unavailable') {
    return 'ASK Seoul 기상 데이터 서비스에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.';
  }
  if (typeof structuredError !== 'string' && structuredError?.code === 'provider_rate_limited') {
    return 'AI 모델의 요청 한도에 도달했습니다. 잠시 후 또는 사용량이 초기화된 뒤 다시 시도해 주세요.';
  }
  return '에이전트 실행 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.';
}

function currentTimeLabel(): string {
  return new Intl.DateTimeFormat('ko-KR', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: true,
  }).format(new Date());
}

function todayLabel(): string {
  const parts = new Intl.DateTimeFormat('ko-KR', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    weekday: 'short',
  }).formatToParts(new Date());
  const get = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? '';
  return `오늘 ${get('year')}-${get('month')}-${get('day')} (${get('weekday')})`;
}

export default function App() {
  const [availableProducts, setAvailableProducts] = useState<WeatherProduct[]>([]);
  const [selectedProductId, setSelectedProductId] = useState('');
  const [question, setQuestion] = useState('');
  const [submittedQuestion, setSubmittedQuestion] = useState('');
  const [submittedAt, setSubmittedAt] = useState('');
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [answer, setAnswer] = useState('');
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [providerMode, setProviderMode] = useState<ProviderMode | 'not connected'>('not connected');
  const [metaState, setMetaState] = useState<MetaState>('loading');
  const [metaError, setMetaError] = useState('');
  const [traceId, setTraceId] = useState('');
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [showExecution, setShowExecution] = useState(false);

  const canSubmit = metaState === 'ready' && question.trim().length > 0 && !isLoading;
  const answerContent = useMemo(() => splitAnswerNotice(answer), [answer]);
  const latestStatus = useMemo(() => {
    const statusEvent = [...events].reverse().find((event) => typeof event.data.status === 'string');
    const status = statusEvent?.data.status;
    return status ? formatStatus(status) : (isLoading ? '답변을 준비하고 있어요' : '대기 중');
  }, [events, isLoading]);

  useEffect(() => {
    let active = true;

    async function loadMeta() {
      try {
        const response = await fetch('/api/v1/meta', {
          headers: { Accept: 'application/json' },
        });
        if (!response.ok) throw new Error('meta request failed');
        const meta = parseServerMeta(await response.json());
        if (!active) return;

        const products = meta.supportedProductIds
          .map((productId) => WEATHER_PRODUCTS_BY_ID.get(productId))
          .filter((product): product is WeatherProduct => Boolean(product));
        setProviderMode(meta.providerMode);
        setAvailableProducts(products);
        if (products[0]) {
          setSelectedProductId(products[0].productId);
          setQuestion(products[0].question);
        }

        if (!meta.ready) {
          const providerName = meta.providerMode === 'gemini'
            ? 'Gemini'
            : meta.providerMode === 'anthropic' ? 'Anthropic' : '데모';
          setMetaState('not-ready');
          setMetaError(`현재 ${providerName} 에이전트를 사용할 수 없습니다. 서버 설정을 확인해 주세요.`);
          return;
        }
        setMetaState('ready');
        setMetaError('');
      } catch {
        if (!active) return;
        setAvailableProducts([]);
        setSelectedProductId('');
        setQuestion('');
        setMetaState('degraded');
        setMetaError('기상 에이전트 연결 상태를 확인하지 못했습니다. 새로고침 후 다시 시도해 주세요.');
      }
    }

    void loadMeta();
    return () => {
      active = false;
    };
  }, []);

  function chooseProduct(product: WeatherProduct) {
    setSelectedProductId(product.productId);
    setQuestion(product.question);
  }

  function handleTabKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (metaState !== 'ready' || availableProducts.length === 0) return;
    let nextIndex: number | null = null;
    if (event.key === 'ArrowRight') nextIndex = (index + 1) % availableProducts.length;
    if (event.key === 'ArrowLeft') nextIndex = (index - 1 + availableProducts.length) % availableProducts.length;
    if (event.key === 'Home') nextIndex = 0;
    if (event.key === 'End') nextIndex = availableProducts.length - 1;
    if (nextIndex === null) return;

    event.preventDefault();
    const tabs = event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]');
    chooseProduct(availableProducts[nextIndex]);
    tabs?.[nextIndex]?.focus();
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedQuestion = question.trim();
    if (!trimmedQuestion || isLoading || metaState !== 'ready') return;

    setSubmittedQuestion(trimmedQuestion);
    setSubmittedAt(currentTimeLabel());
    setEvents([]);
    setAnswer('');
    setEvidence([]);
    setTraceId('');
    setError('');
    setShowExecution(false);
    setIsLoading(true);

    try {
      const response = await fetch('/api/v1/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: trimmedQuestion }),
      });

      let eventId = 0;
      await readAgentStream(response, (eventName, payload) => {
        setEvents((current) => [...current, { id: eventId, name: eventName, data: payload }]);
        eventId += 1;
        if (payload.trace_id) setTraceId(payload.trace_id);
        if (payload.provider_mode && ['demo', 'gemini', 'anthropic'].includes(payload.provider_mode)) {
          setProviderMode(payload.provider_mode as ProviderMode);
        }
        if (eventName === 'tool.result' && Array.isArray(payload.evidence)) {
          setEvidence((current) => [...current, ...payload.evidence!]);
        }
        if (eventName === 'assistant.final') {
          setAnswer(typeof payload.envelope?.answer === 'string' ? payload.envelope.answer : '');
          if (Array.isArray(payload.envelope?.evidence)) setEvidence(payload.envelope.evidence);
        }
        if (eventName === 'session.error') setError(sessionErrorMessage(payload));
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '예상하지 못한 화면 오류가 발생했습니다.');
    } finally {
      setIsLoading(false);
    }
  }

  const composer = (
    <form
      id="question-composer"
      className={submittedQuestion ? 'question-form followup-form' : 'question-form'}
      aria-label="기상 질문"
      onSubmit={handleSubmit}
    >
      <label className="sr-only" htmlFor="question">질문</label>
      <textarea
        id="question"
        value={question}
        onChange={(event) => setQuestion(event.target.value)}
        placeholder="서울의 현재 날씨, 예보 변화, 비·눈 또는 위험 시간대를 물어보세요"
        maxLength={500}
        rows={2}
        disabled={metaState !== 'ready'}
      />
      <button type="submit" className="send-button" disabled={!canSubmit} aria-label="질문 보내기">
        {isLoading
          ? <CircleNotch size={22} weight="bold" className="spin" aria-hidden />
          : <PaperPlaneTilt size={22} weight="fill" aria-hidden />}
        <span>{isLoading ? '답변 중' : '보내기'}</span>
      </button>
    </form>
  );

  const conversation = (
    <section className="conversation" aria-label="기상 데이터 대화" aria-live="polite">
      {!submittedQuestion ? (
        <div className="empty-conversation">
          <Database size={28} weight="duotone" aria-hidden />
          <p>질문을 보내면 답변과 사용한 데이터 근거를 함께 보여드려요.</p>
        </div>
      ) : (
        <>
          <article className="message user-message" aria-label="내 질문">
            <div className="message-meta user-meta"><strong>나</strong><time>{submittedAt}</time></div>
            <p>{submittedQuestion}</p>
          </article>

          <article className="message assistant-message" aria-label="서울 기상 데이터 답변">
            <div className="message-meta assistant-meta">
              <strong>서울 기상 데이터</strong>
              <span>{latestStatus}</span>
            </div>
            <div className="assistant-card">
              {isLoading && !answer ? (
                <div className="loading-answer">
                  <CircleNotch size={22} weight="bold" className="spin" aria-hidden />
                  <span>기상 제품을 찾고 근거를 확인하고 있어요.</span>
                </div>
              ) : null}

              {answerContent.answer ? (
                <div className="answer-summary" role="region" aria-label="핵심 요약">
                  <div className="summary-label">
                    <CheckCircle size={22} weight="duotone" aria-hidden />
                    <strong>핵심 요약</strong>
                  </div>
                  <p>{answerContent.answer}</p>
                </div>
              ) : null}

              {answerContent.notice ? (
                <aside className="answer-notice" role="note">{answerContent.notice}</aside>
              ) : null}

              {evidence.length ? (
                <section className="evidence-region" aria-label="근거 확인">
                  <div className="evidence-heading">
                    <div>
                      <h2>근거 확인</h2>
                      <p>실제 답변에 사용된 {evidence.length}개 기상 제품의 최신 미리보기입니다.</p>
                    </div>
                    <span>{evidence.reduce((total, item) => total + (typeof item.row_count === 'number' ? item.row_count : item.rows?.length ?? 0), 0)}행</span>
                  </div>
                  <div className="evidence-list">
                    {evidence.map((item, index) => (
                      <EvidenceProduct
                        key={`${item.product_id ?? 'evidence'}-${index}`}
                        evidence={item}
                        initiallyOpen={index === 0}
                      />
                    ))}
                  </div>
                </section>
              ) : null}
            </div>
          </article>
        </>
      )}
    </section>
  );

  return (
    <main className="app-shell">
      <section className="workspace" aria-labelledby="app-title">
        <header className="topbar">
          <div className="title-group">
            <h1 id="app-title">서울 기상 데이터에게 물어보세요</h1>
            <p>
              {metaState === 'loading'
                ? '운영 제품 범위를 확인하고 있습니다'
                : availableProducts.length > 0
                  ? `운영 중인 기상 제품 ${availableProducts.length}개만 조회합니다`
                  : '운영 제품 범위를 불러오지 못했습니다'}
            </p>
          </div>
          <div className="header-context">
            <time dateTime={new Date().toISOString().slice(0, 10)}>{todayLabel()}</time>
            <span
              className={`mode-switch mode-${providerMode.replace(' ', '-')}`}
              role="status"
              aria-label={`현재 실행 모드: ${formatProviderMode(providerMode)}`}
              aria-live="polite"
            >
              <span data-active={providerMode === 'demo'}>데모 모드</span>
              <span data-active={providerMode === 'gemini' || providerMode === 'anthropic'}>LIVE 모드</span>
            </span>
          </div>
        </header>

        {availableProducts.length > 0 ? (
          <nav className="intent-tabs" role="tablist" aria-label="기상 질문 유형" aria-orientation="horizontal">
            {availableProducts.map((product, index) => {
              const selected = selectedProductId === product.productId;
              return (
                <button
                  id={`weather-tab-${product.productId}`}
                  key={product.productId}
                  type="button"
                  role="tab"
                  aria-selected={selected}
                  aria-controls="weather-intent-panel"
                  tabIndex={selected ? 0 : -1}
                  disabled={metaState !== 'ready'}
                  className={selected ? 'intent-tab is-selected' : 'intent-tab'}
                  onClick={() => chooseProduct(product)}
                  onKeyDown={(event) => handleTabKeyDown(event, index)}
                >
                  <WeatherIcon kind={product.icon} />
                  <span>
                    <strong>{product.label}</strong>
                    <small>{product.description}</small>
                  </span>
                </button>
              );
            })}
          </nav>
        ) : (
          <div className="scope-state" aria-live="polite">
            {metaState === 'loading' ? '운영 중인 기상 제품을 확인하고 있어요.' : '사용 가능한 기상 제품을 표시할 수 없습니다.'}
          </div>
        )}

        {metaError ? <div className="error-panel" role="alert">{metaError}</div> : null}

        {availableProducts.length > 0 ? (
          <section
            id="weather-intent-panel"
            className="intent-panel"
            role="tabpanel"
            aria-labelledby={`weather-tab-${selectedProductId}`}
          >
            {!submittedQuestion ? (
              <>
                <p className="conversation-hint">원하는 항목을 선택하거나, 자연스럽게 질문해 주세요.</p>
                {composer}
              </>
            ) : null}
            {error ? <div className="error-panel" role="alert">{error}</div> : null}
            {conversation}
            {submittedQuestion ? (
              <div className="followup-composer">
                <p>다른 기상 질문을 이어서 물어보세요.</p>
                {composer}
              </div>
            ) : null}
          </section>
        ) : (
          <>
            {composer}
            {conversation}
          </>
        )}

        <section className="execution-footer" aria-label="에이전트 실행 과정">
          <button
            type="button"
            className="execution-toggle"
            aria-expanded={showExecution}
            onClick={() => setShowExecution((current) => !current)}
          >
            <span>{showExecution ? '실행 과정 접기' : '실행 과정 보기'}</span>
            <CaretDown size={18} weight="bold" aria-hidden />
          </button>
          <div className="execution-panel" hidden={!showExecution}>
            <div className="execution-meta">
              <strong>{traceId ? `추적 ID ${traceId}` : '아직 생성된 추적 ID가 없습니다'}</strong>
              <span>이벤트 {events.length}개</span>
            </div>
            {events.length ? (
              <ol className="timeline">
                {events.map((item) => (
                  <li key={`${item.name}-${item.id}`}>
                    <span className="timeline-dot" aria-hidden />
                    <strong>{EVENT_LABELS[item.name] ?? '에이전트 이벤트'}</strong>
                    <span>{formatTimelineDetail(item.data)}</span>
                  </li>
                ))}
              </ol>
            ) : <p className="empty-state">질문을 보내면 검색과 근거 조회 과정을 확인할 수 있습니다.</p>}
          </div>
        </section>

        <footer className="disclaimer" role="note">
          {providerMode === 'demo'
            ? '데모 모드는 실제 예측이나 경보 발령을 위한 시스템이 아닙니다. '
            : '답변은 ASK Seoul 공개 미리보기 근거를 사용합니다. '}
          최신 기상 특보는 기상청 날씨누리를 함께 확인해 주세요.
        </footer>
      </section>
    </main>
  );
}
