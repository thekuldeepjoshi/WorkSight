import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  Activity, ArrowUpRight, Bell, ChevronDown, CircleHelp,
  Clock3, Download, ExternalLink, Factory, FileUp, Gauge, Layers3, Play, ScanLine,
  Search, Settings2, ShieldCheck, Users, Video,
} from 'lucide-react';
import './styles.css';
import './imbalance.css';
import './ops-timeline.css';
import './focus-areas.css';
import './theme.css';

const ANNOTATED_VIDEO_URL = '/evidence/65f9f144df966bde_annotated_browser.mp4';
const SAVED_IMBALANCE_WINDOWS = [
  { start: '04:00', end: '04:30', matchingFrames: '541/600', share: '90.17%', samples: [
    { time: '04:00', seconds: 240, present: 4, active: 1 },
    { time: '04:15', seconds: 255, present: 3, active: 0 },
    { time: '04:29', seconds: 269, present: 6, active: 1 },
  ] },
  { start: '04:30', end: '05:00', matchingFrames: '596/600', share: '99.33%', samples: [
    { time: '04:30', seconds: 270, present: 6, active: 1 },
    { time: '04:45', seconds: 285, present: 5, active: 1 },
    { time: '04:59', seconds: 299, present: 4, active: 0 },
  ] },
  { start: '06:00', end: '06:30', matchingFrames: '565/600', share: '94.17%', samples: [
    { time: '06:00', seconds: 360, present: 4, active: 0 },
    { time: '06:15', seconds: 375, present: 4, active: 0 },
    { time: '06:29', seconds: 389, present: 5, active: 1 },
  ] },
];

function parseCSV(text) {
  const rows = [];
  let row = [], cell = '', quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    if (char === '"' && quoted && text[i + 1] === '"') { cell += '"'; i += 1; }
    else if (char === '"') quoted = !quoted;
    else if (char === ',' && !quoted) { row.push(cell.trim()); cell = ''; }
    else if ((char === '\n' || char === '\r') && !quoted) {
      if (char === '\r' && text[i + 1] === '\n') i += 1;
      row.push(cell.trim());
      if (row.some(Boolean)) rows.push(row);
      row = []; cell = '';
    } else cell += char;
  }
  row.push(cell.trim());
  if (row.some(Boolean)) rows.push(row);
  if (quoted) return { error: 'CSV contains an unclosed quotation mark. Export it again from WorkSight.' };
  if (rows.length < 2) return { error: 'The CSV has no event rows. Export the reviewable events from WorkSight.' };
  const headers = rows.shift().map((h) => h.replace(/^\uFEFF/, '').toLowerCase());
  const at = (...names) => names.map((name) => headers.indexOf(name)).find((i) => i !== -1);
  const timeAt = at('time', 'timestamp', 'start time', 'start');
  const trackAt = at('track', 'track id');
  const signalAt = at('signal', 'event type', 'event');
  const durationAt = at('duration (s)', 'duration', 'duration seconds');
  const confidenceAt = at('confidence', 'score');
  const evidenceAt = at('evidence rule', 'evidence', 'rule');
  const interpretationAt = at('interpretation', 'why it matters');
  if ([timeAt, trackAt, signalAt, durationAt, confidenceAt].some((i) => i === undefined)) {
    return { error: 'CSV columns do not match WorkSight export. Required: Time, Track, Signal, Duration (s), Confidence.' };
  }
  const parsed = [];
  for (let i = 0; i < rows.length; i += 1) {
    const cells = rows[i];
    const stamp = cells[timeAt] || '';
    const bits = stamp.split(':').map(Number);
    if (![2, 3].includes(bits.length) || bits.some((n) => !Number.isInteger(n) || n < 0) || bits.at(-1) >= 60 || (bits.length === 3 && bits[1] >= 60)) {
      return { error: `Invalid event time on CSV row ${i + 2}: “${stamp}”. Use mm:ss or hh:mm:ss.` };
    }
    const seconds = bits.length === 3 ? bits[0] * 3600 + bits[1] * 60 + bits[2] : bits[0] * 60 + bits[1];
    const signal = (cells[signalAt] || '').toLowerCase().replaceAll('_', ' ').trim();
    if (!signal) return { error: `Missing signal on CSV row ${i + 2}.` };
    const duration = Number(cells[durationAt]);
    let confidence = Number((cells[confidenceAt] || '').replace('%', ''));
    if (cells[confidenceAt]?.includes('%')) confidence /= 100;
    else if (confidence > 1 && confidence <= 100) confidence /= 100;
    if (!Number.isFinite(duration) || duration <= 0 || !Number.isFinite(confidence) || confidence < 0 || confidence > 1) {
      return { error: `Invalid duration or confidence on CSV row ${i + 2}.` };
    }
    if (!cells[trackAt]) return { error: `Missing track ID on CSV row ${i + 2}.` };
    parsed.push({ id: i + 1, label: stamp, seconds, track: cells[trackAt], signal, duration, confidence, evidence: evidenceAt === undefined ? '' : (cells[evidenceAt] || ''), interpretation: interpretationAt === undefined ? '' : (cells[interpretationAt] || '') });
  }
  return parsed.length ? { events: parsed.sort((a, b) => a.seconds - b.seconds) } : { error: 'No valid events were found in this CSV.' };
}

function App() {
  const [screen, setScreen] = useState('upload');
  const [overviewTab, setOverviewTab] = useState('ideal');
  const [events, setEvents] = useState([]);
  const [source, setSource] = useState('');
  const [videoUrl, setVideoUrl] = useState('');
  const [videoSeconds, setVideoSeconds] = useState(0);
  const [csvName, setCsvName] = useState('');
  const [csvError, setCsvError] = useState('');
  const [videoError, setVideoError] = useState('');
  const [videoValid, setVideoValid] = useState(false);
  const [active, setActive] = useState(null);
  const [activeWindow, setActiveWindow] = useState(null);
  const [notice, setNotice] = useState('Add your WorkSight events CSV and video to load the operations report.');
  const videoRef = useRef(null);
  const csvInput = useRef(null);
  const videoInput = useRef(null);
  const wasDataReady = useRef(false);
  const csvValid = events.length > 0 && !csvError;
  const latestEventTime = events.reduce((latest, event) => Math.max(latest, event.seconds + event.duration), 0);
  const timestampsMatch = !videoValid || latestEventTime <= videoSeconds + 1;
  const dataReady = csvValid && videoValid && timestampsMatch;

  useEffect(() => {
    const becameReady = dataReady && !wasDataReady.current;
    wasDataReady.current = dataReady;
    if (screen === 'upload' && becameReady) {
      setScreen('overview');
      setOverviewTab('ideal');
    }
  }, [dataReady, screen]);

  const beltEvents = useMemo(() => events.filter((e) => ['possible belt interaction', 'possible hand belt proximity', 'candidate working with package'].includes(e.signal)), [events]);
  const bottleneckEvents = useMemo(() => events.filter((e) => ['candidate belt bottleneck', 'candidate waiting full belt'].includes(e.signal)), [events]);
  const availabilityEvents = useMemo(() => events.filter((e) => e.signal === 'candidate available no belt demand'), [events]);
  const staffingPressureEvents = useMemo(() => events.filter((e) => e.signal === 'candidate staffing pressure'), [events]);
  const safetyEvents = useMemo(() => events.filter((e) => e.signal.includes('throwing') || e.signal.includes('conveyor zone')), [events]);
  const presenceEvents = useMemo(() => events.filter((e) => e.signal.includes('work area')), [events]);
  const tracks = useMemo(() => [...new Set(events.map((e) => e.track).filter((t) => t !== '—'))], [events]);
  const videoDuration = videoSeconds;
  const bins = useMemo(() => {
    const count = Math.max(1, Math.ceil(videoDuration / 60));
    return Array.from({ length: count }, (_, minute) => ({ minute, value: beltEvents.filter((e) => Math.floor(e.seconds / 60) === minute).length }));
  }, [beltEvents, videoDuration]);
  const peak = Math.max(1, ...bins.map((b) => b.value));
  const avgDuration = beltEvents.length ? beltEvents.reduce((sum, e) => sum + e.duration, 0) / beltEvents.length : 0;
  const gaps = beltEvents.slice(1).map((event, i) => {
    const previous = beltEvents[i];
    const start = previous.seconds + previous.duration;
    return { seconds: Math.max(0, event.seconds - start), start, end: event.seconds };
  }).filter((gap) => gap.seconds > 0);
  const longestGap = gaps.sort((a, b) => b.seconds - a.seconds)[0];
  const operationsWindows = useMemo(() => {
    if (!videoDuration) return [];
    const intervalSecondsByTrack = (rows, start, end) => {
      const byTrack = new Map();
      rows.forEach((event) => {
        if (!event.track || event.track === '—') return;
        const from = Math.max(start, event.seconds);
        const to = Math.min(end, event.seconds + event.duration);
        if (to <= from) return;
        if (!byTrack.has(event.track)) byTrack.set(event.track, []);
        byTrack.get(event.track).push([from, to]);
      });
      let total = 0;
      byTrack.forEach((intervals) => {
        intervals.sort((a, b) => a[0] - b[0]);
        let [from, to] = intervals[0];
        intervals.slice(1).forEach(([nextFrom, nextTo]) => {
          if (nextFrom <= to) to = Math.max(to, nextTo);
          else { total += to - from; from = nextFrom; to = nextTo; }
        });
        total += to - from;
      });
      return Math.round(total);
    };
    const countTracks = (rows, start, end) => new Set(rows
      .filter((event) => event.seconds < end && event.seconds + event.duration > start)
      .map((event) => event.track)
      .filter((track) => track && track !== '—')).size;

    return Array.from({ length: Math.ceil(videoDuration / 30) }, (_, index) => {
      const start = index * 30;
      const end = Math.min(videoDuration, start + 30);
      const activeEvents = beltEvents.filter((event) => event.seconds < end && event.seconds + event.duration > start);
      const startedEvents = beltEvents.filter((event) => event.seconds >= start && event.seconds < end);
      const presentWorkers = countTracks(presenceEvents, start, end);
      const activeWorkers = countTracks(activeEvents, start, end);
      return {
        start, end, presentWorkers, activeWorkers,
        interactionCount: startedEvents.length,
        presenceSeconds: intervalSecondsByTrack(presenceEvents, start, end),
        candidateSeconds: intervalSecondsByTrack(activeEvents, start, end),
        meanConfidence: activeEvents.length ? activeEvents.reduce((sum, event) => sum + event.confidence, 0) / activeEvents.length : null,
        reviewOpportunity: presentWorkers >= 3 && activeWorkers <= 1,
      };
    });
  }, [beltEvents, presenceEvents, videoDuration]);
  const workerSummaries = useMemo(() => {
    const unionSeconds = (intervals) => {
      const ordered = intervals.filter(([start, end]) => end > start).sort((a, b) => a[0] - b[0]);
      if (!ordered.length) return 0;
      let [start, end] = ordered[0];
      let total = 0;
      ordered.slice(1).forEach(([nextStart, nextEnd]) => {
        if (nextStart <= end) end = Math.max(end, nextEnd);
        else { total += end - start; start = nextStart; end = nextEnd; }
      });
      return Math.round(total + end - start);
    };
    const ids = [...new Set([...presenceEvents, ...beltEvents].map((event) => event.track).filter((track) => track && track !== '—'))];
    return ids.map((track) => {
      const present = presenceEvents.filter((event) => event.track === track);
      const belt = beltEvents.filter((event) => event.track === track);
      const available = availabilityEvents.filter((event) => event.track === track);
      const presenceIntervals = present.map((event) => [event.seconds, event.seconds + event.duration]);
      const overlapIntervals = present.flatMap((presence) => belt.map((activity) => [
        Math.max(presence.seconds, activity.seconds),
        Math.min(presence.seconds + presence.duration, activity.seconds + activity.duration),
      ]));
      const presenceSeconds = unionSeconds(presenceIntervals);
      const candidateSeconds = unionSeconds(overlapIntervals);
      return {
        track, presenceSeconds, candidateSeconds, beltEventCount: belt.length,
        availableCandidate: available.length > 0,
        activityShare: presenceSeconds ? candidateSeconds / presenceSeconds : 0,
        sampleEvent: belt[0] || available[0] || present[0] || null,
      };
    }).sort((a, b) => b.presenceSeconds - a.presenceSeconds || b.candidateSeconds - a.candidateSeconds);
  }, [availabilityEvents, beltEvents, presenceEvents]);
  const totalPresencePersonSeconds = workerSummaries.reduce((sum, worker) => sum + worker.presenceSeconds, 0);
  const totalCandidatePersonSeconds = workerSummaries.reduce((sum, worker) => sum + worker.candidateSeconds, 0);
  const candidateActivityShare = totalPresencePersonSeconds ? totalCandidatePersonSeconds / totalPresencePersonSeconds : null;
  const workersWithPresence = workerSummaries.filter((worker) => worker.presenceSeconds > 0);
  const workersWithCandidateActivity = workerSummaries.filter((worker) => worker.candidateSeconds > 0).length;
  const eventRatePerMinute = videoDuration ? beltEvents.length / (videoDuration / 60) : 0;
  const resourceReviewWindows = operationsWindows.filter((period) => period.presentWorkers >= 3 && period.activeWorkers <= 1)
    .sort((a, b) => b.presentWorkers - a.presentWorkers || a.activeWorkers - b.activeWorkers || b.presenceSeconds - a.presenceSeconds);
  const lowActivityWindows = operationsWindows.filter((period) => period.presentWorkers > 0 && period.activeWorkers === 0)
    .sort((a, b) => b.presenceSeconds - a.presenceSeconds || a.start - b.start);
  const allRiskReviewEvents = [...safetyEvents]
    .sort((a, b) => b.duration - a.duration)
  const riskReviewEvents = allRiskReviewEvents
    .slice(0, 5);
  const activeEvent = events.find((event) => event.id === active) || null;
  const fmt = (n) => `${Math.floor(n / 60)}:${String(Math.floor(n % 60)).padStart(2, '0')}`;

  const seekTo = (seconds, label, eventId = null, windowStart = null) => {
    setActive(eventId);
    setActiveWindow(windowStart);
    const player = videoRef.current;
    if (!player || !videoValid) { setNotice('Video is not ready yet. Wait for both files to validate before opening an event.'); return; }
    const duration = Number.isFinite(player.duration) ? player.duration : videoSeconds;
    player.currentTime = Math.max(0, Math.min(seconds, duration));
    document.querySelector('#review')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    player.play().catch(() => setNotice(`Video positioned at ${label}. Press play to review the evidence.`));
  };
  const seek = (event) => seekTo(event.seconds, event.label, event.id);
  const loadCSV = async (file) => {
    if (!file) return;
    setScreen('upload');
    setEvents([]); setCsvError('Validating CSV…'); setCsvName(file.name); setActive(null);
    const result = parseCSV(await file.text());
    if (result.error) { setCsvError(result.error); setNotice(result.error); return; }
    setEvents(result.events); setCsvError(''); setNotice(videoValid ? `Validated ${result.events.length} event rows in ${file.name}.` : `Validated ${result.events.length} event rows in ${file.name}. Now validate a video.`);
  };
  const loadVideo = (file) => {
    if (!file) return;
    setScreen('upload');
    setVideoValid(false); setVideoError('Checking video format and duration…'); setVideoSeconds(0); setActive(null);
    if (videoUrl?.startsWith('blob:')) URL.revokeObjectURL(videoUrl);
    setSource(file.name);
    if (!/\.(mp4|webm|mov)$/i.test(file.name) || file.size === 0) {
      setVideoUrl(''); setVideoError('Choose a non-empty MP4, WebM, or MOV video. AVI is not supported by browser playback.'); return;
    }
    if (!/annotated/i.test(file.name)) {
      setVideoUrl(''); setVideoError('This looks like the original video. Choose the annotated output from data/processed so the overlays are visible.'); return;
    }
    setVideoUrl(URL.createObjectURL(file)); setNotice(`Checking ${file.name}…`);
  };
  const markVideoReady = () => {
    const player = videoRef.current;
    if (!player || !Number.isFinite(player.duration) || player.duration <= 0) {
      setVideoError('The video has no readable duration. Choose another video export.'); return;
    }
    setVideoSeconds(player.duration); setVideoValid(true); setVideoError('');
    setNotice(`Validated ${source} (${fmt(player.duration)}).`);
  };
  const downloadReview = () => {
    const rows = [['Time', 'Track', 'Signal', 'Duration (s)', 'Confidence', 'Evidence rule', 'Interpretation'], ...events.map((e) => [e.label, e.track, e.signal, e.duration, e.confidence, e.evidence, e.interpretation])];
    const csv = rows.map((r) => r.map((v) => `"${String(v).replaceAll('"', '""')}"`).join(',')).join('\n');
    const link = document.createElement('a'); link.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' })); link.download = 'worksight-review.csv'; link.click(); URL.revokeObjectURL(link.href);
  };

  return <div className={`app-shell ${screen === 'upload' ? 'upload-app-shell' : ''}`}>
    {screen !== 'upload' && <aside className="sidebar">
      <div className="brand"><div className="brand-mark"><ScanLine size={19}/></div><span>worksight<span className="brand-dot">.</span></span></div>
      <div className="site-switch"><span className="site-icon"><Factory size={16}/></span><span><b>North dock</b><small>Operations site</small></span><ChevronDown size={15}/></div>
      <div className="nav-label">WORKSPACE</div>
      <button className={`nav-item ${screen === 'overview' ? 'selected' : ''}`} onClick={() => setScreen('overview')}><Gauge size={17}/> Operations overview</button>
      <button className={`nav-item ${screen === 'ideal-candidates' ? 'selected' : ''}`} onClick={() => setScreen('ideal-candidates')}><Users size={17}/> Staffing review</button>
      <button className="nav-item" onClick={() => { setScreen('overview'); window.setTimeout(() => document.querySelector('#review')?.scrollIntoView({ behavior: 'smooth' }), 50); }}><Video size={17}/> Video review</button>
      <button className="nav-item" onClick={() => { setScreen('upload'); window.setTimeout(() => csvInput.current?.click(), 50); }}><Layers3 size={17}/> Change files <FileUp size={14} className="nav-trailing"/></button>
      <div className="side-bottom"><div className="privacy"><ShieldCheck size={16}/><span><b>Private by design</b><small>Files stay in this browser session</small></span></div><button className="nav-item"><Settings2 size={17}/> Settings</button><div className="profile"><div className="avatar">KM</div><span><b>Operations lead</b><small>Local workspace</small></span><ChevronDown size={14}/></div></div>
    </aside>}

    <main className={`main ${screen === 'upload' ? 'upload-main' : ''}`}>
      {screen !== 'upload' && <header className="topbar"><div className="crumb">Operations <span>/</span> {screen === 'ideal-candidates' ? 'Staffing review' : 'Overview'} <span className="live-tag"><i/> {screen === 'ideal-candidates' ? 'SAVED ANALYSIS' : 'OPERATIONS'}</span></div><div className="top-actions"><button className="icon-button" aria-label="Search"><Search size={17}/></button><button className="icon-button notification" aria-label="Notifications"><Bell size={17}/><i/></button><span className="top-date">Shift review · Today</span></div></header>}
      <div className={`content ${screen === 'upload' ? 'upload-content' : ''}`}>
        <input ref={csvInput} type="file" accept=".csv,text/csv" hidden onChange={(e) => { loadCSV(e.target.files?.[0]); e.target.value = ''; }}/>
        <input ref={videoInput} type="file" accept=".mp4,.webm,.mov,video/mp4,video/webm,video/quicktime" hidden onChange={(e) => { loadVideo(e.target.files?.[0]); e.target.value = ''; }}/>
        {screen === 'upload' && <section className="upload-welcome"><div className="brand"><div className="brand-mark"><ScanLine size={19}/></div><span>worksight<span className="brand-dot">.</span></span></div><div className="eyebrow">VIDEO OPERATIONS WORKSPACE</div><h1>{dataReady ? 'Your files are ready' : 'Upload your analysis files'}</h1><p>{dataReady ? `${csvName} and ${source} are validated for this browser session.` : 'Choose your WorkSight event CSV and browser-ready annotated video. Once both are validated, the operations overview opens automatically.'}</p>{dataReady && <button className="button primary upload-return" onClick={() => setScreen('overview')}>Return to operations overview</button>}</section>}
        {screen !== 'upload' && <section className="page-heading"><div><div className="eyebrow">{screen === 'ideal-candidates' ? 'STAFFING OPPORTUNITIES' : 'SHIFT INTELLIGENCE'} <span className="green-pip"/></div><h1>{screen === 'ideal-candidates' ? 'Staffing review' : 'Operations overview'}</h1><p>{screen === 'ideal-candidates' ? 'Review periods with high work-area presence and low candidate belt activity.' : 'See where work is flowing, where it pauses, and what may need a closer look.'}</p></div><div className="heading-buttons">{screen === 'overview' && <><button className="button secondary" onClick={() => { setScreen('upload'); window.setTimeout(() => csvInput.current?.click(), 0); }}><FileUp size={15}/> Change files</button><button className="button primary" onClick={() => { setScreen('upload'); window.setTimeout(() => videoInput.current?.click(), 0); }}><Video size={15}/> Change video</button></>}</div></section>}
        {screen !== 'upload' && <div className="data-notice"><span className="notice-icon"><Activity size={15}/></span><span>{screen === 'ideal-candidates' ? 'Saved results from the earlier completed video run. Each timestamp opens the annotated evidence.' : notice}</span>{screen !== 'ideal-candidates' && <button onClick={() => setNotice('Signals are estimates from video. Confirm important events before acting.') }><CircleHelp size={15}/></button>}</div>}
        {screen === 'ideal-candidates' && dataReady ? <section className="panel imbalance-report" id="saved-windows">
          <div className="imbalance-heading">
            <div><div className="eyebrow">EARLIER COMPLETED ANALYSIS</div><h2>High presence, low candidate belt activity</h2><p>Three 30-second windows met the rule: at least 3 workers in the work area and no more than 1 with candidate belt activity.</p></div>
            <a className="button secondary saved-video-link" href={ANNOTATED_VIDEO_URL} target="_blank" rel="noreferrer"><Video size={14}/> Open annotated video <ExternalLink size={13}/></a>
          </div>
          <div className="imbalance-window-list">{SAVED_IMBALANCE_WINDOWS.map((period, index) => <article className="imbalance-window" key={period.start}>
            <div className="imbalance-period"><span>WINDOW 0{index + 1}</span><b>{period.start}–{period.end}</b><small>30 seconds</small></div>
            <div className="imbalance-coverage"><span>Rule-matching frames</span><b>{period.matchingFrames}</b><small>{period.share} of tracked-person observations</small></div>
            <div className="imbalance-samples"><span>Evidence timestamps <small>· open video at timestamp</small></span><div>{period.samples.map((sample) => <a key={sample.time} href={`${ANNOTATED_VIDEO_URL}#t=${sample.seconds}`} target="_blank" rel="noreferrer" title={`Open annotated video at ${sample.time}`}><b>{sample.time}</b><small>{sample.present} present · {sample.active} candidate-active</small><ExternalLink size={12}/></a>)}</div></div>
          </article>)}</div>
          <div className="imbalance-caveat"><ShieldCheck size={14}/><span>Candidate activity is the wrist-near-belt signal; it does not confirm unloading or prove that other workers were idle.</span></div>
        </section> : screen === 'upload' || !dataReady ? <section className="upload-gate">
          <div className="gate-heading"><span className="eyebrow">{dataReady ? 'FILES VALIDATED' : 'GET STARTED'}</span><h2>{dataReady ? 'Your files are ready' : 'Add both analysis files'}</h2><p>{dataReady ? 'Replace either file below, or return to the operations overview.' : 'The report opens after WorkSight validates the event CSV and confirms the annotated video plays and covers all event times.'}</p></div>
          <div className="file-checks">
            <article className={`file-check ${csvValid ? 'file-valid' : csvError && csvError !== 'Validating CSV…' ? 'file-invalid' : ''}`}><div className="file-check-top"><span className="file-check-icon"><Layers3 size={19}/></span><span className="file-number">01</span></div><h3>Events CSV</h3><p>WorkSight export with Time, Track, Signal, Duration (s), and Confidence.</p><button className="button secondary" onClick={() => csvInput.current?.click()}><FileUp size={14}/>{csvValid ? 'Choose another CSV' : 'Choose CSV file'}</button><div className="file-status">{csvValid ? <ShieldCheck size={14}/> : <CircleHelp size={14}/>}<span>{csvValid ? `${csvName} · ${events.length} valid events` : csvError || 'Required · CSV format not checked yet'}</span></div></article>
            <article className={`file-check ${videoValid ? 'file-valid' : videoError && videoError !== 'Checking video format and duration…' ? 'file-invalid' : ''}`}><div className="file-check-top"><span className="file-check-icon"><Video size={19}/></span><span className="file-number">02</span></div><h3>Video evidence</h3><p>Load the browser-ready annotated MP4 to see zones and detections at each event.</p><button className="button secondary" onClick={() => videoInput.current?.click()}><Video size={14}/>{videoValid ? 'Choose another video' : 'Choose video file'}</button><div className="file-status">{videoValid ? <ShieldCheck size={14}/> : <CircleHelp size={14}/>}<span>{videoValid ? `${source} · ${fmt(videoSeconds)} · playable` : videoError || 'Required · MP4, WebM, or MOV'}</span></div>{videoUrl && <video className="validation-player" ref={videoRef} src={videoUrl} controls playsInline preload="metadata" onLoadedMetadata={(e) => setVideoSeconds(e.currentTarget.duration || 0)} onCanPlay={markVideoReady} onError={() => { setVideoValid(false); setVideoError(`Browser cannot decode ${source}. Choose the H.264 *_annotated_browser.mp4 file.`); }}/>}</article>
          </div>
          {csvValid && videoValid && !timestampsMatch && <div className="validation-warning"><CircleHelp size={16}/> CSV/video mismatch: last event is at {fmt(latestEventTime)}, but video is {fmt(videoSeconds)}. Load the matching video.</div>}
          <div className="gate-footer"><ShieldCheck size={15}/><span>Files stay in this browser session. No report data appears until both files pass validation.</span></div>
        </section> : <>
        <nav className="overview-tabs" aria-label="Operations overview tabs">
          <a className={overviewTab === 'ideal' ? 'tab-active' : ''} href="#worker-utilisation" onClick={() => setOverviewTab('ideal')}>Ideal employee</a>
          <a className={overviewTab === 'belt' ? 'tab-active' : ''} href="#belt-utilisation" onClick={() => setOverviewTab('belt')}>Belt utilisation</a>
          <a className={overviewTab === 'resources' ? 'tab-active' : ''} href="#resource-allocation" onClick={() => setOverviewTab('resources')}>Resource allocation</a>
          <a className={overviewTab === 'bottlenecks' ? 'tab-active' : ''} href="#operational-bottlenecks" onClick={() => setOverviewTab('bottlenecks')}>Bottlenecks</a>
          <a className={overviewTab === 'risks' ? 'tab-active' : ''} href="#risk-review" onClick={() => setOverviewTab('risks')}>Risks & safety</a>
          <a className={overviewTab === 'video' ? 'tab-active' : ''} href="#review" onClick={() => setOverviewTab('video')}>Video review</a>
          <a className={overviewTab === 'timeline' ? 'tab-active' : ''} href="#activity-timeline" onClick={() => setOverviewTab('timeline')}>Activity timeline</a>
        </nav>
          <section className="panel focus-panel operational-rules-panel">
            <div className="focus-panel-heading"><span className="focus-icon lilac"><Activity size={17}/></span><div><div className="eyebrow">PACKAGE FLOW RULES</div><h2>Operational review candidates</h2><p>Click a rule signal to jump to its supporting video moment.</p></div></div>
            <div className="focus-stat-row operational-rule-stats">
              <button type="button" className="focus-stat-button" disabled={!availabilityEvents.length} onClick={() => availabilityEvents[0] && seek(availabilityEvents[0])}><small>Available · no belt demand</small><b>{availabilityEvents.length}</b></button>
              <button type="button" className="focus-stat-button" disabled={!bottleneckEvents.length} onClick={() => bottleneckEvents[0] && seek(bottleneckEvents[0])}><small>Belt slowdown / waiting</small><b>{bottleneckEvents.length}</b></button>
              <button type="button" className="focus-stat-button" disabled={!staffingPressureEvents.length} onClick={() => staffingPressureEvents[0] && seek(staffingPressureEvents[0])}><small>Possible staffing pressure</small><b>{staffingPressureEvents.length}</b></button>
              <button type="button" className="focus-stat-button" disabled={!safetyEvents.length} onClick={() => safetyEvents[0] && seek(safetyEvents[0])}><small>Safety review cues</small><b>{safetyEvents.length}</b></button>
            </div>
            <div className="focus-note">Rules use tracked box detections, pose/wrist proximity and movement between frames. “Full” currently means four visible package tracks; staffing pressure means three or more boxes with two or more candidate-active workers. These prototype thresholds need calibration to your camera and belt.</div>
          </section>

          <div className="focus-area-grid">
          <section className="panel focus-panel" id="worker-utilisation">
            <div className="focus-panel-heading"><span className="focus-icon green"><Users size={17}/></span><div><div className="eyebrow">01 · WORKER UTILISATION</div><h2>Activity signals by worker</h2><p>Compare work-area presence with candidate belt activity.</p></div></div>
            <div className="focus-stat-row"><div><small>Tracks with presence</small><b>{workersWithPresence.length}</b></div><div><small>Tracks with signal in presence</small><b>{workersWithCandidateActivity}</b></div><div><small>Signal share of presence time</small><b>{candidateActivityShare === null ? '—' : `${Math.round(candidateActivityShare * 100)}%`}</b></div></div>
            <div className="focus-list-heading"><span>ANONYMOUS TRACK</span><span>AREA PRESENCE</span><span>BELT SIGNAL DURING PRESENCE</span><span>STATUS</span></div>
            <div className="focus-list">{workersWithPresence.slice(0, 6).map((worker) => <button type="button" key={worker.track} className="focus-list-row worker-focus-row" onClick={() => worker.sampleEvent && seek(worker.sampleEvent)} title={`Jump to evidence for track ${worker.track}`}><b>T{worker.track}</b><span>{fmt(worker.presenceSeconds)}</span><span>{fmt(worker.candidateSeconds)}</span><em>{worker.candidateSeconds > 0 ? 'Candidate handling' : worker.availableCandidate ? 'Available · no belt demand' : 'No working signal detected'}</em></button>)}{workersWithPresence.length === 0 && <div className="focus-empty">No work-area presence rows were found in this CSV.</div>}</div>
            <div className="focus-note">A worker is marked available only when no package is detected on the belt and no package is estimated near the wrist. A detected package near the hand makes the worker not available. This is not an employee ranking; track IDs are temporary.</div>
          </section>

          <section className="panel focus-panel" id="belt-utilisation">
            <div className="focus-panel-heading"><span className="focus-icon blue"><Activity size={17}/></span><div><div className="eyebrow">02 · BELT UTILISATION</div><h2>Conveyor activity proxy</h2><p>Wrist-near-belt detections summarize possible interactions.</p></div></div>
            <div className="focus-stat-row belt-stat-row"><div><small>Candidate events</small><b>{beltEvents.length}</b><small>{events.length ? `${Math.round(beltEvents.length / events.length * 100)}% of reviewable signals` : 'No reviewable signals'}</small></div><div><small>Events per minute</small><b>{eventRatePerMinute.toFixed(1)}</b></div><div><small>Mean event duration</small><b>{avgDuration.toFixed(1)} sec</b></div><div><small>Candidate time in presence</small><b>{fmt(totalCandidatePersonSeconds)}</b></div></div>
            {beltEvents[0] && <button className="focus-text-button belt-review-button" onClick={() => seek(beltEvents[0])}>Review a candidate belt event <ArrowUpRight size={13}/></button>}
            <div className="measure-status-list"><div><b>Package count</b><span className="measure-available">Sampled proxy</span><small>Rule events include the highest sampled box count during the interval.</small></div><div><b>Area occupancy / throughput</b><span className="measure-unavailable">Not measured</span><small>Needs belt surface segmentation and a calibrated crossing line.</small></div></div>
            <div className="focus-note">YOLO-World package detections are sampled at about 3 fps. This gives an approximate count, not belt-area occupancy or verified throughput.</div>
          </section>

          <section className="panel focus-panel" id="resource-allocation">
            <div className="focus-panel-heading"><span className="focus-icon lilac"><Layers3 size={17}/></span><div><div className="eyebrow">03 · RESOURCE ALLOCATION</div><h2>Potential staffing review</h2><p>30-second intervals with at least 3 presence tracks and at most 1 belt-signal track.</p></div></div>
            <div className="focus-stat-row one-stat"><div><small>Package backlog + active handling cues</small><b>{staffingPressureEvents.length}</b></div><button className="focus-stat-button" disabled={!staffingPressureEvents.length} onClick={() => staffingPressureEvents[0] && seek(staffingPressureEvents[0])}><small>Open first staffing pressure cue</small><b>{staffingPressureEvents.length ? staffingPressureEvents[0].label : '—'}</b></button><button className="focus-text-button" onClick={() => { setScreen('ideal-candidates'); window.scrollTo({ top: 0, behavior: 'smooth' }); }}>Open presence review <ArrowUpRight size={13}/></button></div>
            <div className="focus-list">{resourceReviewWindows.slice(0, 4).map((period) => <button type="button" key={period.start} className="focus-list-row period-focus-row" onClick={() => seekTo(period.start, `${fmt(period.start)} staffing review`, null, period.start)}><b>{fmt(period.start)}–{fmt(period.end)}</b><span>{period.presentWorkers} tracks seen</span><span>{period.activeWorkers} with belt signal</span><em>Review</em></button>)}{resourceReviewWindows.length === 0 && <div className="focus-empty">No candidate intervals match this screen’s CSV-based rule.</div>}</div>
            <div className="focus-note">Counts are distinct tracks observed anywhere within each 30-second interval, not simultaneous headcount. Use these as review candidates; a staffing recommendation also needs sustained demand or backlog evidence.</div>
          </section>

          <section className="panel focus-panel" id="operational-bottlenecks">
            <div className="focus-panel-heading"><span className="focus-icon amber"><Clock3 size={17}/></span><div><div className="eyebrow">04 · OPERATIONAL BOTTLENECKS</div><h2>Packages waiting without handling cues</h2><p>Package detections on the belt with no package-hand or approach signal.</p></div></div>
            <div className="focus-stat-row"><div><small>Bottleneck / full-belt waiting candidates</small><b>{bottleneckEvents.length}</b></div><button type="button" className="focus-stat-button" disabled={!bottleneckEvents.length} onClick={() => bottleneckEvents[0] && seek(bottleneckEvents[0])}><small>Open first evidence cue</small><b>{bottleneckEvents.length ? bottleneckEvents[0].label : '—'}</b></button><div><small>Low-signal intervals</small><b>{lowActivityWindows.length}</b></div></div>
            <div className="focus-list">{lowActivityWindows.slice(0, 4).map((period) => <button type="button" key={period.start} className="focus-list-row bottleneck-focus-row" onClick={() => seekTo(period.start, `${fmt(period.start)} low-activity interval`, null, period.start)}><b>{fmt(period.start)}–{fmt(period.end)}</b><span>{period.presentWorkers} tracks seen</span><span>{fmt(period.presenceSeconds)} person-s</span><em>Open video</em></button>)}{lowActivityWindows.length === 0 && <div className="focus-empty">No low-signal interval with recorded presence was found.</div>}</div>
            <div className="focus-note">A belt package with no assigned hand/carry/approach cue is marked as a possible slowdown. Item overlap, missed detections and occlusion can make the association wrong.</div>
          </section>

          <section className="panel focus-panel risk-focus-panel" id="risk-review">
            <div className="focus-panel-heading"><span className="focus-icon coral"><ShieldCheck size={17}/></span><div><div className="eyebrow">05 · RISK & SAFETY REVIEW</div><h2>Potential throwing or conveyor entry</h2><p>Review rapid package motion near hands and worker feet inside the conveyor zone.</p></div><span className="count-pill">{safetyEvents.length} candidates</span></div>
            <div className="risk-event-list">{riskReviewEvents.map((event) => <button type="button" key={event.id} className="risk-event-row" onClick={() => seek(event)}><span><b>{event.label}</b><small>Track T{event.track} · {event.duration.toFixed(1)} sec · {event.signal}</small></span><Play size={13}/></button>)}{riskReviewEvents.length === 0 && <div className="focus-empty">No candidate conveyor-zone signals are available for review.</div>}</div>
            <div className="focus-note">A rapid box track near a wrist is only a throwing candidate. A foot/ankle in the belt zone is a major-risk review cue. Sitting/standing on the belt is not yet reliably classified; confirm visually before calling any event a hazard.</div>
          </section>
        </div>

        <section className="panel ops-timeline-panel" id="activity-timeline">
          <div className="panel-heading"><div><div className="eyebrow">30-SECOND OPERATING RHYTHM</div><h2>People and belt activity</h2><p>Presence and candidate activity by interval. Click any row to jump to that point in the video.</p></div><span className="count-pill">{operationsWindows.length} windows</span></div>
          <div className="ops-timeline-scroll"><div className="ops-timeline-table">
            <div className="ops-timeline-header"><span>TIME</span><span>IN AREA</span><span>BELT ACTIVE</span><span>EVENT STARTS</span><span>AREA PERSON-s</span><span>BELT PERSON-s</span><span>MEAN PERSON CONF.</span><span>REVIEW</span></div>
            {operationsWindows.map((period) => <button type="button" key={period.start} className={`ops-timeline-row ${period.reviewOpportunity ? 'review-opportunity' : ''} ${activeWindow === period.start ? 'window-selected' : ''}`} onClick={() => seekTo(period.start, `${fmt(period.start)} activity window`, null, period.start)} title={`Jump to ${fmt(period.start)}. ${period.presentWorkers} workers in area, ${period.activeWorkers} with candidate belt activity.`}>
              <b>{fmt(period.start)}–{fmt(period.end)}</b><span>{period.presentWorkers}</span><span>{period.activeWorkers}</span><span>{period.interactionCount}</span><span>{period.presenceSeconds}</span><span>{period.candidateSeconds}</span><span>{period.meanConfidence === null ? '—' : `${Math.round(period.meanConfidence * 100)}%`}</span><span>{period.reviewOpportunity ? <i className="review-opportunity-tag">Review opportunity</i> : <i className="open-video-tag">Open video</i>}</span>
            </button>)}
          </div></div>
          <div className="ops-timeline-note"><ShieldCheck size={14}/><span>Worker counts use distinct track IDs within each interval. Durations are deduplicated per track; candidate belt activity is the wrist-near-belt signal, not verified unloading. Confidence is person detector confidence, not event certainty.</span></div>
        </section>

        <section className="insight-grid">
          <article className="panel flow-panel"><div className="panel-heading"><div><div className="eyebrow">ACTIVITY RHYTHM</div><h2>Conveyor interaction signals</h2><p>Candidate wrist-zone events, grouped by minute</p></div><span className="range-chip"><span/> Full video</span></div><div className="chart-wrap"><div className="chart-y"><span>{peak}</span><span>{Math.ceil(peak/2)}</span><span>0</span></div><div className="chart-body"><div className="chart-grid"><i/><i/><i/></div><div className="bars">{bins.map((b) => <button key={b.minute} className={`bar-column ${Math.floor((active ? (events.find(e=>e.id===active)?.seconds||0) : -1)/60)===b.minute?'bar-active':''}`} title={`${fmt(b.minute*60)} · ${b.value} possible interactions`} onClick={() => { const target = beltEvents.find(e => Math.floor(e.seconds/60) === b.minute); if(target) seek(target); }}><span style={{height:`${b.value ? Math.max(6,b.value/peak*100) : 2}%`}}/><small>{fmt(b.minute*60)}</small></button>)}</div></div></div><div className="chart-legend"><span><i className="legend-dot"/> Possible wrist-to-belt interactions</span><span className="chart-caveat">No event means “not detected,” not proof of inactivity.</span></div></article>
          <article className="panel insights-panel"><div className="panel-heading"><div><div className="eyebrow">WHAT TO REVIEW</div><h2>Operational signals</h2></div><span className="count-pill">{Math.min(3, beltEvents.length)} items</span></div><div className="insight-list">
            {longestGap && longestGap.seconds >= 45 && <button className="insight-row" onClick={() => seekTo(longestGap.start, `${fmt(longestGap.start)} quiet interval`)}><span className="insight-symbol amber"><Clock3 size={16}/></span><span className="insight-copy"><b>Extended pause in detected activity</b><small>{fmt(longestGap.start)}–{fmt(longestGap.end)} · {fmt(longestGap.seconds)} without a candidate interaction</small><em>Review the clip for waiting, occlusion, or missed detection.</em></span><ArrowUpRight size={15}/></button>}
            {beltEvents.length > 0 && <button className="insight-row" onClick={() => seek(beltEvents[0])}><span className="insight-symbol blue"><Activity size={16}/></span><span className="insight-copy"><b>{beltEvents.length} candidate interactions detected</b><small>{tracks.length} anonymous track{tracks.length===1?'':'s'} show belt-zone activity</small><em>Use sampled clips to confirm the signal before changing staffing.</em></span><ArrowUpRight size={15}/></button>}
            {events.some(e=>e.signal.includes('work area')) && <button className="insight-row" onClick={() => seek(events.find(e=>e.signal.includes('work area')))}><span className="insight-symbol purple"><Users size={16}/></span><span className="insight-copy"><b>Workers were present in the work area</b><small>{events.filter(e=>e.signal.includes('work area')).length} presence windows · duration is summed by track</small><em>Presence alone cannot distinguish active work from waiting.</em></span><ArrowUpRight size={15}/></button>}
            {events.length===0 && <div className="empty-insights"><Activity size={19}/><b>Load a review CSV to surface signals</b><span>Use the event export from WorkSight.</span></div>}
          </div><div className="insight-footer"><ShieldCheck size={14}/> Each insight links to video evidence</div></article>
        </section>

        <section className="lower-grid" id="review">
          <article className="panel video-panel"><div className="panel-heading"><div><div className="eyebrow">EVIDENCE REVIEW</div><h2>Video & event timeline</h2></div><button className="small-button" onClick={() => videoInput.current?.click()}><Video size={14}/> Replace video</button></div>{activeEvent && <div className="event-evidence-card"><b>{activeEvent.signal} · {activeEvent.label}</b>{activeEvent.evidence && <span>{activeEvent.evidence}</span>}{activeEvent.interpretation && <small>{activeEvent.interpretation}</small>}</div>}<div className="video-frame"><video ref={videoRef} src={videoUrl} controls playsInline preload="metadata" onCanPlay={markVideoReady} onError={() => { setVideoValid(false); setVideoError(`Browser cannot decode ${source}. Choose the H.264 *_annotated_browser.mp4 file.`); }} onTimeUpdate={(e)=>{const current=events.find(ev=>Math.abs(ev.seconds-e.currentTarget.currentTime)<1.5); if(current)setActive(current.id);}}/><div className="video-caption"><span className="video-live-dot"/> {source}</div></div><div className="timeline-head"><span>EVENT TIMELINE</span><span>{events.length} signals</span></div><div className="timeline-track"><div className="timeline-axis"><span>00:00</span><span>{fmt(videoDuration/2)}</span><span>{fmt(videoDuration)}</span></div><div className="timeline-rail">{events.map(e=><button key={e.id} className={`event-mark ${e.signal.includes('belt')?'belt-mark':'work-mark'} ${active===e.id?'mark-active':''}`} style={{left:`${Math.min(99,Math.max(0,e.seconds/videoDuration*100))}%`}} title={`${e.label} · ${e.signal}`} onClick={()=>seek(e)}/>)}</div></div><div className="video-legend"><span><i className="legend-dot belt-dot"/> Belt interaction candidate</span><span><i className="legend-dot work-dot"/> Work-area presence</span></div></article>
          <article className="panel event-panel"><div className="panel-heading"><div><div className="eyebrow">REVIEW QUEUE</div><h2>Events to inspect</h2></div><span className="count-pill">{events.length} total</span></div><div className="event-table-head"><span>TIME / SIGNAL</span><span>TRACK</span><span>CONF.</span></div><div className="event-list">{[...events].sort((a,b)=>a.seconds-b.seconds).slice(0,8).map(e=><button key={e.id} className={`event-row ${active===e.id?'event-selected':''}`} onClick={()=>seek(e)}><span><b>{e.label}</b><small><i className={e.signal.includes('belt')?'dot-belt':'dot-work'}/>{e.signal}</small></span><span className="track-chip">T{e.track}</span><span className="confidence">{e.confidence ? `${Math.round(e.confidence*100)}%` : '—'}<Play size={12}/></span></button>)}{events.length===0&&<div className="empty-table">Your uploaded event rows will appear here.</div>}</div><button className="view-all" onClick={downloadReview}>Download all review events <Download size={14}/></button></article>
        </section>
        <footer className="footnote"><span><ShieldCheck size={14}/> Local analysis · no personal identification</span><span><CircleHelp size={14}/> A video-derived signal is an estimate. Verify before acting.</span></footer>
        </>}
      </div>
    </main>
  </div>;
}

createRoot(document.getElementById('root')).render(<App/>);
